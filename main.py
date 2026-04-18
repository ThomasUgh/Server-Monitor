#!/usr/bin/env python3
"""Server Monitor – main entry point."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

import psutil

from monitor.checks import ExternalChecker
from monitor.config import load_config
from monitor.embed import EmbedBuilder
from monitor.events import Event, EventManager
from monitor.journal import JournalCollector
from monitor.metrics import MetricsCollector, SystemMetrics
from monitor.notifier import DiscordNotifier
from monitor.persistence import StateManager
from monitor.services import ServiceChecker
from monitor.utils import format_uptime, setup_logging

logger = logging.getLogger("server_monitor.main")


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

class ServerMonitor:

    def __init__(self, config_path: str) -> None:
        self._cfg = load_config(config_path)
        setup_logging(self._cfg.log_file, self._cfg.log_level)

        self._state = StateManager(self._cfg.state_file)
        self._events = EventManager(self._cfg)
        self._metrics = MetricsCollector(self._cfg)
        self._journal = JournalCollector(self._cfg)
        self._services = ServiceChecker(self._cfg)
        self._checks = ExternalChecker(self._cfg)
        self._embed = EmbedBuilder(self._cfg)
        self._notifier = DiscordNotifier(self._cfg, self._state)

        self._running = False
        self._last_collect: float = 0
        self._last_discord_update: float = 0
        self._last_service_check: float = 0
        self._last_external_check: float = 0

        # Latest snapshots (populated during run)
        self._current_metrics: Optional[SystemMetrics] = None
        self._current_service_statuses = []

        # Restore events from state
        self._events.load_from_state(self._state.get_events())

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum, _frame) -> None:
        logger.info("Received signal %d – shutting down", signum)
        self._running = False

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info("Server Monitor starting up")
        self._install_signal_handlers()
        self._running = True

        self._detect_and_emit_restart_events()
        self._state.update_run_time()

        # Initial collect + push before entering the loop
        self._do_collect(startup=True)
        self._do_discord_update(force=True)

        self._run_loop()
        self._shutdown()

    def _shutdown(self) -> None:
        logger.info("Saving state and exiting")
        self._state.set_events(self._events.to_state())
        self._state.update_run_time()
        self._state.flush()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        while self._running:
            now = time.time()

            if now - self._last_collect >= self._cfg.collect_interval_seconds:
                self._do_collect(startup=False)

            if self._events.pending_immediate:
                logger.debug("Immediate Discord update triggered by high-severity event")
                self._do_discord_update(force=True)

            if now - self._last_discord_update >= self._cfg.update_interval_seconds:
                self._do_discord_update(force=False)

            time.sleep(5)

    # ------------------------------------------------------------------
    # Collection cycle
    # ------------------------------------------------------------------

    def _do_collect(self, startup: bool = False) -> None:
        now = time.time()
        self._last_collect = now

        # 1. System metrics
        try:
            self._current_metrics = self._metrics.collect()
        except Exception as exc:
            logger.error("Metrics collection failed: %s", exc)
            self._current_metrics = None

        # 2. Sustained threshold checks
        if self._current_metrics:
            self._check_sustained_thresholds()
            self._check_instant_thresholds(self._current_metrics)

        # 3. Journal events
        self._collect_journal_events(startup=startup)

        # 4. Service checks (every 3 collect cycles ≈ 90s by default)
        if now - self._last_service_check >= self._cfg.collect_interval_seconds * 3:
            self._check_services()
            self._last_service_check = now

        # 5. External checks (every 5 collect cycles ≈ 150s by default)
        if now - self._last_external_check >= self._cfg.collect_interval_seconds * 5:
            self._run_external_checks()
            self._last_external_check = now

        self._state.set_events(self._events.to_state())

    # ------------------------------------------------------------------
    # Threshold evaluation
    # ------------------------------------------------------------------

    def _check_sustained_thresholds(self) -> None:
        alerts = self._metrics.check_sustained_thresholds()
        for alert in alerts:
            cooldown = self._cfg.dedupe.resource_cooldown_minutes
            if self._state.is_on_cooldown(alert.key, cooldown):
                continue

            severity = "critical" if alert.value >= alert.threshold * 1.1 else "warning"
            title = (
                f"{alert.metric} bei ⌀{alert.value}% – "
                f"Schwelle {alert.threshold}% über {alert.duration_minutes:.0f} Min. überschritten"
            )
            if "Netzwerk" in alert.metric:
                avg, peak = self._metrics.get_peak_net_in_window()
                title = (
                    f"{alert.metric}: ⌀{avg} Mbit/s (Peak {peak} Mbit/s) "
                    f"über {alert.duration_minutes:.0f} Min., Schwelle {alert.threshold} Mbit/s"
                )

            event = Event(
                timestamp=time.time(),
                severity=severity,
                key=alert.key,
                title=title,
            )
            if self._events.add(event):
                self._state.set_cooldown(alert.key)
                logger.info("Sustained threshold event: %s", title)

    def _check_instant_thresholds(self, m: SystemMetrics) -> None:
        """Instant (non-sustained) checks for disk and swap."""
        thresh = self._cfg.thresholds

        # Disk – instant alert when over threshold (no duration required)
        for disk in m.disks:
            if disk.percent >= thresh.disk_percent:
                key = f"disk_full:{disk.mountpoint}"
                cooldown = self._cfg.dedupe.resource_cooldown_minutes
                if not self._state.is_on_cooldown(key, cooldown):
                    severity = "critical" if disk.percent >= 95 else "warning"
                    event = Event(
                        timestamp=time.time(),
                        severity=severity,
                        key=key,
                        title=(
                            f"Disk {disk.mountpoint}: {disk.percent}% belegt "
                            f"({disk.used_gb:.0f}/{disk.total_gb:.0f} GB) – Schwelle {thresh.disk_percent}%"
                        ),
                    )
                    if self._events.add(event):
                        self._state.set_cooldown(key)
                        logger.info("Disk threshold event: %s%%  %s", disk.percent, disk.mountpoint)

        # Swap – instant alert
        if m.swap_total_gb > 0 and m.swap_percent >= thresh.swap_percent:
            key = "swap_high"
            if not self._state.is_on_cooldown(key, self._cfg.dedupe.resource_cooldown_minutes):
                event = Event(
                    timestamp=time.time(),
                    severity="warning",
                    key=key,
                    title=f"Swap-Nutzung bei {m.swap_percent}% ({m.swap_used_gb:.1f}/{m.swap_total_gb:.1f} GB)",
                )
                if self._events.add(event):
                    self._state.set_cooldown(key)

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _collect_journal_events(self, startup: bool = False) -> None:
        try:
            entries, ooms = self._journal.collect_recent(is_startup=startup)
        except Exception as exc:
            logger.error("Journal collection failed: %s", exc)
            return

        cooldown = self._cfg.dedupe.journal_cooldown_minutes

        for entry in entries:
            key = self._journal.format_event_key(entry)
            if self._state.is_on_cooldown(key, cooldown):
                continue
            event = Event(
                timestamp=entry.timestamp,
                severity=entry.priority,
                key=key,
                title=self._journal.format_title(entry),
            )
            if self._events.add(event):
                self._state.set_cooldown(key)

        for oom in ooms:
            key = f"oom:{oom.unit}:{int(oom.timestamp)}"
            if self._state.is_on_cooldown(key, cooldown):
                continue
            event = Event(
                timestamp=oom.timestamp,
                severity="critical",
                key=key,
                title=f"OOM-Kill erkannt: {oom.message[:100]}",
            )
            if self._events.add(event):
                self._state.set_cooldown(key)
                logger.warning("OOM kill detected: %s", oom.message[:100])

    # ------------------------------------------------------------------
    # Service checks
    # ------------------------------------------------------------------

    def _check_services(self) -> None:
        try:
            statuses = self._services.check_all()
        except Exception as exc:
            logger.error("Service check failed: %s", exc)
            return

        self._current_service_statuses = statuses
        cooldown = self._cfg.dedupe.service_cooldown_minutes

        for s in statuses:
            if s.ok:
                continue
            key = f"service:{s.name}:{s.active_state}"
            if self._state.is_on_cooldown(key, cooldown):
                continue
            event = Event(
                timestamp=time.time(),
                severity=s.severity,
                key=key,
                title=f"Service {s.name}: {s.active_state} ({s.sub_state})",
            )
            if self._events.add(event):
                self._state.set_cooldown(key)
                logger.warning("Service issue: %s – %s", s.name, s.active_state)

    # ------------------------------------------------------------------
    # External checks (HTTP / port / cert)
    # ------------------------------------------------------------------

    def _run_external_checks(self) -> None:
        cooldown = self._cfg.dedupe.check_cooldown_minutes

        # HTTP
        for result in self._checks.run_http_checks():
            if result.ok:
                continue
            key = f"http:{result.url}"
            if self._state.is_on_cooldown(key, cooldown):
                continue
            event = Event(
                timestamp=time.time(),
                severity="error",
                key=key,
                title=f"HTTP-Check fehlgeschlagen: {result.name} – {result.error}",
            )
            if self._events.add(event):
                self._state.set_cooldown(key)

        # Port
        for result in self._checks.run_port_checks():
            if result.ok:
                continue
            key = f"port:{result.host}:{result.port}"
            if self._state.is_on_cooldown(key, cooldown):
                continue
            event = Event(
                timestamp=time.time(),
                severity="error",
                key=key,
                title=f"Port-Check fehlgeschlagen: {result.name} – {result.error}",
            )
            if self._events.add(event):
                self._state.set_cooldown(key)

        # Certificates
        for result in self._checks.run_cert_checks():
            if result.days_remaining is None:
                key = f"cert_error:{result.host}"
                if not self._state.is_on_cooldown(key, cooldown):
                    event = Event(
                        timestamp=time.time(),
                        severity="critical",
                        key=key,
                        title=f"Zertifikat-Check Fehler: {result.host} – {result.error}",
                    )
                    if self._events.add(event):
                        self._state.set_cooldown(key)
                continue

            cert_cfg = next((c for c in self._cfg.cert_checks if c.host == result.host), None)
            warn_days = cert_cfg.warning_days if cert_cfg else 30
            crit_days = cert_cfg.critical_days if cert_cfg else 7

            if result.days_remaining <= 0:
                severity = "critical"
                title = f"Zertifikat ABGELAUFEN: {result.host} (seit {abs(result.days_remaining)} Tagen)"
            elif result.days_remaining <= crit_days:
                severity = "critical"
                title = f"Zertifikat läuft in {result.days_remaining} Tagen ab: {result.host}"
            elif result.days_remaining <= warn_days:
                severity = "warning"
                title = f"Zertifikat läuft in {result.days_remaining} Tagen ab: {result.host}"
            else:
                continue

            key = f"cert:{result.host}:{result.days_remaining // 7}"
            if not self._state.is_on_cooldown(key, cooldown * 6):  # longer cooldown for certs
                event = Event(
                    timestamp=time.time(),
                    severity=severity,
                    key=key,
                    title=title,
                )
                if self._events.add(event):
                    self._state.set_cooldown(key)

    # ------------------------------------------------------------------
    # Restart detection
    # ------------------------------------------------------------------

    def _detect_and_emit_restart_events(self) -> None:
        """Emit one-time events for host reboot and monitor restart."""
        try:
            current_boot = psutil.boot_time()
        except Exception:
            current_boot = None

        saved_boot = self._state.last_boot_time
        saved_run = self._state.last_run_time
        now = time.time()

        # -- Host reboot detection --
        if self._cfg.notify_on_reboot and current_boot is not None:
            if saved_boot is None:
                # First run ever – just record boot time
                pass
            elif abs(current_boot - saved_boot) > 30:
                # Boot time changed → reboot
                key = f"host_reboot:{int(current_boot)}"
                cooldown = self._cfg.dedupe.reboot_cooldown_minutes
                if not self._state.is_on_cooldown(key, cooldown):
                    boot_str = datetime.fromtimestamp(current_boot).strftime("%d.%m.%Y %H:%M")
                    event = Event(
                        timestamp=now,
                        severity="info",
                        key=key,
                        title=f"🔁 Host wurde neu gestartet (Boot: {boot_str})",
                    )
                    self._events.add(event)
                    self._state.set_cooldown(key)
                    logger.info("Host reboot detected (boot_time changed)")

            # Always update saved boot time
            self._state.last_boot_time = current_boot

        # -- Monitor restart / unexpected stop detection --
        if self._cfg.notify_on_monitor_restart and saved_run is not None:
            gap_minutes = (now - saved_run) / 60
            # If last run was > (collect_interval + 2 min) ago, we had an unexpected stop
            expected_gap = (self._cfg.collect_interval_seconds / 60) + 2
            if gap_minutes > expected_gap:
                key = "monitor_restart"
                cooldown = self._cfg.dedupe.monitor_restart_cooldown_minutes
                if not self._state.is_on_cooldown(key, cooldown):
                    event = Event(
                        timestamp=now,
                        severity="info",
                        key=key,
                        title=f"🔄 Monitor-Dienst neu gestartet (Pause: {gap_minutes:.0f} Min.)",
                    )
                    self._events.add(event)
                    self._state.set_cooldown(key)
                    logger.info("Monitor restart detected (gap %.1f min)", gap_minutes)

    # ------------------------------------------------------------------
    # Discord update
    # ------------------------------------------------------------------

    def _do_discord_update(self, force: bool = False) -> None:
        self._last_discord_update = time.time()
        self._events.clear_pending_immediate()

        overall = self._events.overall_status()
        display_events = self._events.get_display_events()

        payload = self._embed.build(
            metrics=self._current_metrics,
            events=display_events,
            service_statuses=self._current_service_statuses,
            overall_status=overall,
        )

        success = self._notifier.send_or_update(payload)
        if success:
            logger.debug("Discord embed updated (status: %s, events: %d)", overall, len(display_events))
        else:
            logger.error("Discord embed update failed")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Server Monitor – Linux health & event monitoring with Discord webhook",
    )
    parser.add_argument(
        "-c", "--config",
        default="/etc/server-monitor/config.yaml",
        help="Path to YAML config file (default: /etc/server-monitor/config.yaml)",
    )
    args = parser.parse_args()

    if not __import__("pathlib").Path(args.config).exists():
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    try:
        monitor = ServerMonitor(args.config)
        monitor.start()
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        logging.getLogger("server_monitor").exception("Fatal error")
        sys.exit(3)


if __name__ == "__main__":
    main()
