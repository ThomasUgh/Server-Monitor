"""Builds the Discord embed payload from current metrics and events."""
from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from monitor.events import Event
from monitor.metrics import SystemMetrics
from monitor.services import ServiceStatus
from monitor.utils import format_uptime, severity_emoji, truncate

_TZ_BERLIN = ZoneInfo("Europe/Berlin")

_COLOR = {
    "healthy":  0x57F287,
    "warning":  0xFEE75C,
    "critical": 0xED4245,
    "unknown":  0x95A5A6,
}

_STATUS_ICON = {"healthy": "✅", "warning": "⚠️", "critical": "🚨"}
_STATUS_LABEL = {"healthy": "Healthy", "warning": "Warning", "critical": "Critical"}
_SEP = {"name": "\u200b", "value": "\u200b", "inline": False}


def _now_berlin() -> str:
    return datetime.now(_TZ_BERLIN).strftime("%d.%m.%Y %H:%M:%S")


def _bar(percent: float, width: int = 8) -> str:
    filled = max(0, min(width, round(percent / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _disk_emoji(percent: float, threshold: float) -> str:
    if percent >= 95:        return "🔴"
    if percent >= threshold: return "🟠"
    if percent >= threshold * 0.8: return "🟡"
    return "🟢"


class EmbedBuilder:

    def __init__(self, config) -> None:
        self._cfg = config

    def build(
        self,
        metrics,
        events,
        service_statuses,
        overall_status: str,
    ) -> Dict[str, Any]:
        color  = _COLOR.get(overall_status, _COLOR["unknown"])
        hostname = socket.gethostname()
        status_icon  = _STATUS_ICON.get(overall_status, "❔")
        status_label = _STATUS_LABEL.get(overall_status, overall_status.title())

        fields: List[Dict] = []
        if metrics:
            fields += self._system_fields(metrics)
        if service_statuses:
            fields.append(_SEP)
            fields.append(self._service_field(service_statuses))
        fields.append(_SEP)
        fields.append(self._event_field(events))

        return {"embeds": [{
            "title": f"🖥️  {hostname}   {status_icon} {status_label}",
            "color": color,
            "fields": fields,
            "footer": {"text": f"🔄 Zuletzt aktualisiert: {_now_berlin()}"},
        }]}

    # ------------------------------------------------------------------
    def _system_fields(self, m) -> List[Dict]:
        fields = []
        thresh = self._cfg.thresholds

        # CPU
        fields.append({
            "name": "💻  CPU",
            "value": f"**{m.cpu_percent}%** `{_bar(m.cpu_percent)}`\n`{m.cpu_cores}` Kerne",
            "inline": True,
        })

        # RAM
        fields.append({
            "name": "🧠  RAM",
            "value": f"**{m.ram_percent}%** `{_bar(m.ram_percent)}`\n{m.ram_used_gb:.1f} / {m.ram_total_gb:.1f} GB",
            "inline": True,
        })

        # Swap
        if m.swap_total_gb > 0.1:
            fields.append({
                "name": "💾  Swap",
                "value": f"**{m.swap_percent}%** `{_bar(m.swap_percent)}`\n{m.swap_used_gb:.1f} / {m.swap_total_gb:.1f} GB",
                "inline": True,
            })

        # Disks
        for disk in m.disks[:4]:
            fields.append({
                "name": f"{_disk_emoji(disk.percent, thresh.disk_percent)}  Disk `{disk.mountpoint}`",
                "value": f"**{disk.percent}%** `{_bar(disk.percent)}`\n{disk.used_gb:.0f} / {disk.total_gb:.0f} GB",
                "inline": True,
            })

        # I/O Wait
        fields.append({
            "name": "⚡  I/O Wait",
            "value": f"**{m.iowait_percent}%** `{_bar(m.iowait_percent)}`",
            "inline": True,
        })

        # Load Average
        la = m.load_avg
        fields.append({
            "name": "📊  Load Avg",
            "value": f"`{la[0]}` · `{la[1]}` · `{la[2]}`\n1m · 5m · 15m",
            "inline": True,
        })

        # Network
        if self._cfg.network.enabled:
            iface = self._cfg.network.interface
            fields.append({
                "name": f"🌐  Netz `{iface}`",
                "value": (
                    f"↑ **{m.net_mbits_sent}** Mbit/s  ↓ **{m.net_mbits_recv}** Mbit/s\n"
                    f"Gesamt ↑ {m.net_total_sent_gb} GB  ↓ {m.net_total_recv_gb} GB"
                ),
                "inline": True,
            })

        # Uptime
        fields.append({
            "name": "⏱️  Uptime",
            "value": f"**{format_uptime(m.uptime_seconds)}**",
            "inline": True,
        })

        return fields

    # ------------------------------------------------------------------
    def _service_field(self, statuses) -> Dict:
        lines = []
        for s in statuses:
            if s.active_state == "active":         icon = "🟢"
            elif s.severity == "critical":          icon = "🔴"
            elif s.severity == "warning":           icon = "🟡"
            else:                                   icon = "⚪"
            sub = s.sub_state if s.sub_state not in ("unknown", "") else s.active_state
            lines.append(f"{icon} `{s.name}` — {sub}")
        return {
            "name": "🔧  Services",
            "value": truncate("\n".join(lines) or "_Keine Services konfiguriert_", 1020),
            "inline": False,
        }

    # ------------------------------------------------------------------
    def _event_field(self, events) -> Dict:
        if not events:
            return {"name": "📋  Ereignisse", "value": "_Keine aktuellen Ereignisse_", "inline": False}
        lines = []
        for e in events:
            ts = datetime.fromtimestamp(e.timestamp, tz=_TZ_BERLIN).strftime("%d.%m %H:%M")
            lines.append(f"`{ts}` {severity_emoji(e.severity)} {truncate(e.title, 80)}")
        return {
            "name": f"📋  Ereignisse — letzte {len(events)}",
            "value": truncate("\n".join(lines), 1020),
            "inline": False,
        }
