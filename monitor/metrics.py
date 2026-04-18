"""System metrics collection with rolling-window sustained threshold detection."""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import psutil


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DiskInfo:
    mountpoint: str
    used_gb: float
    total_gb: float
    percent: float


@dataclass
class SystemMetrics:
    cpu_percent: float
    cpu_cores: int
    ram_used_gb: float
    ram_total_gb: float
    ram_percent: float
    swap_used_gb: float
    swap_total_gb: float
    swap_percent: float
    disks: List[DiskInfo]
    iowait_percent: float
    load_avg: Tuple[float, float, float]
    uptime_seconds: float
    net_mbits_sent: float       # current sample rate Mbit/s
    net_mbits_recv: float
    net_total_sent_gb: float    # cumulative total since boot
    net_total_recv_gb: float
    boot_time: float


@dataclass
class _WindowPoint:
    timestamp: float
    value: float


@dataclass
class ThresholdAlert:
    key: str
    metric: str
    value: float        # average value in window
    threshold: float
    duration_minutes: float


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Collect system metrics and detect sustained threshold violations."""

    def __init__(self, config) -> None:
        self._cfg = config
        self._thresh = config.thresholds
        self._net_cfg = config.network

        # Rolling windows  (timestamp, value)
        self._w_cpu: Deque[_WindowPoint] = deque()
        self._w_ram: Deque[_WindowPoint] = deque()
        self._w_iowait: Deque[_WindowPoint] = deque()
        self._w_net: Deque[_WindowPoint] = deque()   # total Mbit/s (sent+recv)

        # Network counter tracking
        self._prev_net_bytes_sent: Optional[int] = None
        self._prev_net_bytes_recv: Optional[int] = None
        self._prev_net_time: Optional[float] = None

        # Warm up cpu_percent (first call always returns 0)
        try:
            psutil.cpu_percent(interval=None)
            psutil.cpu_times_percent(interval=None)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> SystemMetrics:
        """Collect a fresh snapshot of all system metrics."""
        now = time.time()

        # CPU
        cpu_pct = psutil.cpu_percent(interval=None)
        cpu_cores = psutil.cpu_count(logical=True) or 1

        # iowait
        cpu_times = psutil.cpu_times_percent(interval=None)
        iowait = getattr(cpu_times, "iowait", 0.0)

        # Memory
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        ram_used_gb = vm.used / 1e9
        ram_total_gb = vm.total / 1e9
        swap_used_gb = sw.used / 1e9
        swap_total_gb = sw.total / 1e9

        # Disks
        disks = self._collect_disks()

        # Network
        net_sent, net_recv, net_total_sent_gb, net_total_recv_gb = self._collect_network(now)

        # Load average
        try:
            la = os.getloadavg()
        except AttributeError:
            la = (0.0, 0.0, 0.0)

        # Boot / uptime
        boot_time = psutil.boot_time()
        uptime = now - boot_time

        metrics = SystemMetrics(
            cpu_percent=round(cpu_pct, 1),
            cpu_cores=cpu_cores,
            ram_used_gb=round(ram_used_gb, 2),
            ram_total_gb=round(ram_total_gb, 2),
            ram_percent=round(vm.percent, 1),
            swap_used_gb=round(swap_used_gb, 2),
            swap_total_gb=round(swap_total_gb, 2),
            swap_percent=round(sw.percent, 1),
            disks=disks,
            iowait_percent=round(iowait, 1),
            load_avg=(round(la[0], 2), round(la[1], 2), round(la[2], 2)),
            uptime_seconds=uptime,
            net_mbits_sent=net_sent,
            net_mbits_recv=net_recv,
            net_total_sent_gb=net_total_sent_gb,
            net_total_recv_gb=net_total_recv_gb,
            boot_time=boot_time,
        )

        # Update windows
        self._push(self._w_cpu, now, cpu_pct, self._thresh.cpu_duration_minutes)
        self._push(self._w_ram, now, vm.percent, self._thresh.ram_duration_minutes)
        self._push(self._w_iowait, now, iowait, self._thresh.iowait_duration_minutes)
        if self._net_cfg.enabled:
            total_mbit = net_sent + net_recv
            self._push(self._w_net, now, total_mbit, self._net_cfg.duration_minutes)

        return metrics

    def check_sustained_thresholds(self) -> List[ThresholdAlert]:
        """Return list of threshold alerts for metrics sustained above limits."""
        alerts: List[ThresholdAlert] = []
        now = time.time()

        def _check(window, threshold, duration_minutes, key, label):
            pts = [p for p in window if now - p.timestamp <= duration_minutes * 60]
            if not pts:
                return
            # Require at least 2 data points to avoid false alarms on startup
            if len(pts) < 2:
                return
            # Window must actually span the required duration
            span_minutes = (pts[0].timestamp - pts[-1].timestamp) / 60
            if span_minutes < duration_minutes * 0.7:
                return
            avg = sum(p.value for p in pts) / len(pts)
            if avg >= threshold:
                alerts.append(ThresholdAlert(
                    key=key,
                    metric=label,
                    value=round(avg, 1),
                    threshold=threshold,
                    duration_minutes=duration_minutes,
                ))

        _check(self._w_cpu, self._thresh.cpu_percent, self._thresh.cpu_duration_minutes,
               "sustained_cpu", "CPU")
        _check(self._w_ram, self._thresh.ram_percent, self._thresh.ram_duration_minutes,
               "sustained_ram", "RAM")
        _check(self._w_iowait, self._thresh.iowait_percent, self._thresh.iowait_duration_minutes,
               "sustained_iowait", "I/O Wait")

        if self._net_cfg.enabled:
            _check(self._w_net, self._net_cfg.threshold_mbits, self._net_cfg.duration_minutes,
                   f"sustained_net_{self._net_cfg.interface}",
                   f"Netzwerk {self._net_cfg.interface}")

        return alerts

    def get_peak_net_in_window(self) -> Tuple[float, float]:
        """Return (avg_mbit, peak_mbit) for current network window."""
        if not self._w_net:
            return 0.0, 0.0
        values = [p.value for p in self._w_net]
        return round(sum(values) / len(values), 1), round(max(values), 1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_disks(self) -> List[DiskInfo]:
        mountpoints = self._cfg.disk_mountpoints
        if not mountpoints:
            # Auto-detect physical partitions (skip tmpfs, devtmpfs, etc.)
            partitions = psutil.disk_partitions(all=False)
            mountpoints = [p.mountpoint for p in partitions
                           if p.fstype not in ("tmpfs", "devtmpfs", "squashfs", "overlay", "proc")]
            # Always include /
            if "/" not in mountpoints:
                mountpoints.insert(0, "/")
            # Limit auto-detect to avoid clutter
            mountpoints = mountpoints[:6]

        disks = []
        for mp in mountpoints:
            try:
                usage = psutil.disk_usage(mp)
                disks.append(DiskInfo(
                    mountpoint=mp,
                    used_gb=round(usage.used / 1e9, 1),
                    total_gb=round(usage.total / 1e9, 1),
                    percent=round(usage.percent, 1),
                ))
            except (PermissionError, FileNotFoundError):
                pass
        return disks

    def _collect_network(self, now: float) -> Tuple[float, float, float, float]:
        """Return (sent_mbit_s, recv_mbit_s, total_sent_gb, total_recv_gb)."""
        try:
            counters = psutil.net_io_counters(pernic=True)
            iface = self._net_cfg.interface
            if iface not in counters:
                totals = psutil.net_io_counters(pernic=False)
                if totals is None:
                    return 0.0, 0.0, 0.0, 0.0
                sent = totals.bytes_sent
                recv = totals.bytes_recv
            else:
                sent = counters[iface].bytes_sent
                recv = counters[iface].bytes_recv

            total_sent_gb = round(sent / 1e9, 2)
            total_recv_gb = round(recv / 1e9, 2)

            if self._prev_net_time is None or self._prev_net_bytes_sent is None:
                self._prev_net_bytes_sent = sent
                self._prev_net_bytes_recv = recv
                self._prev_net_time = now
                return 0.0, 0.0, total_sent_gb, total_recv_gb

            dt = now - self._prev_net_time
            if dt <= 0:
                return 0.0, 0.0, total_sent_gb, total_recv_gb

            d_sent = max(0, sent - self._prev_net_bytes_sent)
            d_recv = max(0, recv - self._prev_net_bytes_recv)

            sent_mbit = round((d_sent * 8) / dt / 1e6, 2)
            recv_mbit = round((d_recv * 8) / dt / 1e6, 2)

            self._prev_net_bytes_sent = sent
            self._prev_net_bytes_recv = recv
            self._prev_net_time = now

            return sent_mbit, recv_mbit, total_sent_gb, total_recv_gb
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

    @staticmethod
    def _push(window: Deque[_WindowPoint], ts: float, value: float, max_minutes: float) -> None:
        window.append(_WindowPoint(timestamp=ts, value=value))
        cutoff = ts - max_minutes * 60 * 2   # keep 2× window for analysis
        while window and window[0].timestamp < cutoff:
            window.popleft()
