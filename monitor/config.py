"""Configuration loading from YAML into typed dataclasses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------

@dataclass
class ThresholdConfig:
    cpu_percent: float = 85.0
    cpu_duration_minutes: float = 10.0
    ram_percent: float = 85.0
    ram_duration_minutes: float = 10.0
    disk_percent: float = 85.0
    swap_percent: float = 80.0
    iowait_percent: float = 25.0
    iowait_duration_minutes: float = 5.0


@dataclass
class NetworkConfig:
    interface: str = "eth0"
    threshold_mbits: float = 100.0
    duration_minutes: float = 10.0
    enabled: bool = True


@dataclass
class ServiceCheckConfig:
    name: str = ""
    critical_on_failed: bool = True
    warn_on_inactive: bool = False


@dataclass
class CertCheckConfig:
    host: str = ""
    port: int = 443
    warning_days: int = 30
    critical_days: int = 7


@dataclass
class HttpCheckConfig:
    url: str = ""
    name: str = ""
    expected_status: int = 200
    timeout: int = 10


@dataclass
class PortCheckConfig:
    host: str = ""
    port: int = 0
    name: str = ""
    timeout: int = 5


@dataclass
class JournalConfig:
    enabled: bool = True
    priorities: List[str] = field(default_factory=lambda: ["err", "crit", "alert", "emerg"])
    lookback_minutes: int = 5
    exclude_units: List[str] = field(default_factory=list)
    include_units: List[str] = field(default_factory=list)
    oom_detection: bool = True


@dataclass
class DedupeConfig:
    resource_cooldown_minutes: int = 10
    service_cooldown_minutes: int = 5
    journal_cooldown_minutes: int = 5
    check_cooldown_minutes: int = 5
    reboot_cooldown_minutes: int = 120
    monitor_restart_cooldown_minutes: int = 60


@dataclass
class Config:
    # Required
    discord_webhook_url: str = ""

    # Intervals
    update_interval_seconds: int = 60
    collect_interval_seconds: int = 30

    # Display
    max_events_displayed: int = 10
    severity_mode: str = "warning"          # info | warning | error | critical
    immediate_update_severity: str = "error"  # severity that triggers instant embed update

    # Monitored disk mountpoints (auto-detect if empty)
    disk_mountpoints: List[str] = field(default_factory=list)

    # Sub-configs
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    monitored_services: List[ServiceCheckConfig] = field(default_factory=list)
    cert_checks: List[CertCheckConfig] = field(default_factory=list)
    http_checks: List[HttpCheckConfig] = field(default_factory=list)
    port_checks: List[PortCheckConfig] = field(default_factory=list)
    journal: JournalConfig = field(default_factory=JournalConfig)
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)

    # Paths
    state_file: str = "/var/lib/server-monitor/state.json"
    log_file: str = "/var/log/server-monitor/monitor.log"
    log_level: str = "INFO"

    # Behaviour flags
    notify_on_reboot: bool = True
    notify_on_monitor_restart: bool = True
    docker_monitoring: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_service_checks(raw: list) -> List[ServiceCheckConfig]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(ServiceCheckConfig(name=item))
        elif isinstance(item, dict):
            result.append(ServiceCheckConfig(
                name=item.get("name", ""),
                critical_on_failed=item.get("critical_on_failed", True),
                warn_on_inactive=item.get("warn_on_inactive", False),
            ))
    return result


def _load_cert_checks(raw: list) -> List[CertCheckConfig]:
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(CertCheckConfig(host=item))
        elif isinstance(item, dict):
            result.append(CertCheckConfig(
                host=item.get("host", ""),
                port=item.get("port", 443),
                warning_days=item.get("warning_days", 30),
                critical_days=item.get("critical_days", 7),
            ))
    return result


def _load_http_checks(raw: list) -> List[HttpCheckConfig]:
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(HttpCheckConfig(
                url=item.get("url", ""),
                name=item.get("name", item.get("url", "")),
                expected_status=item.get("expected_status", 200),
                timeout=item.get("timeout", 10),
            ))
    return result


def _load_port_checks(raw: list) -> List[PortCheckConfig]:
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(PortCheckConfig(
                host=item.get("host", "localhost"),
                port=item.get("port", 0),
                name=item.get("name", f"{item.get('host', 'localhost')}:{item.get('port', 0)}"),
                timeout=item.get("timeout", 5),
            ))
    return result


def load_config(path: str) -> Config:
    """Load and parse YAML config file into a Config dataclass."""
    with open(path, "r", encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    cfg = Config()

    # Top-level scalars
    for key in (
        "discord_webhook_url", "update_interval_seconds", "collect_interval_seconds",
        "max_events_displayed", "severity_mode", "immediate_update_severity",
        "state_file", "log_file", "log_level",
        "notify_on_reboot", "notify_on_monitor_restart", "docker_monitoring",
    ):
        if key in raw:
            setattr(cfg, key, raw[key])

    if "disk_mountpoints" in raw:
        cfg.disk_mountpoints = raw["disk_mountpoints"] or []

    # Thresholds
    if "thresholds" in raw and isinstance(raw["thresholds"], dict):
        t = raw["thresholds"]
        cfg.thresholds = ThresholdConfig(
            cpu_percent=t.get("cpu_percent", 85.0),
            cpu_duration_minutes=t.get("cpu_duration_minutes", 10.0),
            ram_percent=t.get("ram_percent", 85.0),
            ram_duration_minutes=t.get("ram_duration_minutes", 10.0),
            disk_percent=t.get("disk_percent", 85.0),
            swap_percent=t.get("swap_percent", 80.0),
            iowait_percent=t.get("iowait_percent", 25.0),
            iowait_duration_minutes=t.get("iowait_duration_minutes", 5.0),
        )

    # Network
    if "network" in raw and isinstance(raw["network"], dict):
        n = raw["network"]
        cfg.network = NetworkConfig(
            interface=n.get("interface", "eth0"),
            threshold_mbits=n.get("threshold_mbits", 100.0),
            duration_minutes=n.get("duration_minutes", 10.0),
            enabled=n.get("enabled", True),
        )

    # Journal
    if "journal" in raw and isinstance(raw["journal"], dict):
        j = raw["journal"]
        cfg.journal = JournalConfig(
            enabled=j.get("enabled", True),
            priorities=j.get("priorities", ["err", "crit", "alert", "emerg"]),
            lookback_minutes=j.get("lookback_minutes", 5),
            exclude_units=j.get("exclude_units", []),
            include_units=j.get("include_units", []),
            oom_detection=j.get("oom_detection", True),
        )

    # Dedupe / Cooldowns
    if "dedupe" in raw and isinstance(raw["dedupe"], dict):
        d = raw["dedupe"]
        cfg.dedupe = DedupeConfig(
            resource_cooldown_minutes=d.get("resource_cooldown_minutes", 10),
            service_cooldown_minutes=d.get("service_cooldown_minutes", 5),
            journal_cooldown_minutes=d.get("journal_cooldown_minutes", 5),
            check_cooldown_minutes=d.get("check_cooldown_minutes", 5),
            reboot_cooldown_minutes=d.get("reboot_cooldown_minutes", 120),
            monitor_restart_cooldown_minutes=d.get("monitor_restart_cooldown_minutes", 60),
        )

    # Lists
    if "monitored_services" in raw:
        cfg.monitored_services = _load_service_checks(raw["monitored_services"] or [])
    if "cert_checks" in raw:
        cfg.cert_checks = _load_cert_checks(raw["cert_checks"] or [])
    if "http_checks" in raw:
        cfg.http_checks = _load_http_checks(raw["http_checks"] or [])
    if "port_checks" in raw:
        cfg.port_checks = _load_port_checks(raw["port_checks"] or [])

    # Validate required fields
    if not cfg.discord_webhook_url:
        raise ValueError("discord_webhook_url is required in config.yaml")

    return cfg
