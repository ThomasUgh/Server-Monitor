"""Builds the Discord embed payload from current metrics and events."""
from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from monitor.events import Event, EventManager
from monitor.metrics import SystemMetrics
from monitor.services import ServiceStatus
from monitor.utils import format_uptime, severity_emoji, truncate


# ---------------------------------------------------------------------------
# Embed colours
# ---------------------------------------------------------------------------
_COLOR_HEALTHY = 0x2ECC71    # green
_COLOR_WARNING = 0xF39C12    # amber
_COLOR_CRITICAL = 0xE74C3C   # red
_COLOR_UNKNOWN = 0x95A5A6    # grey

_STATUS_ICONS = {
    "healthy": "✅",
    "warning": "⚠️",
    "critical": "🚨",
}

_STATUS_LABELS = {
    "healthy": "Healthy",
    "warning": "Warning",
    "critical": "Critical",
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class EmbedBuilder:

    def __init__(self, config) -> None:
        self._cfg = config

    # ------------------------------------------------------------------

    def build(
        self,
        metrics: Optional[SystemMetrics],
        events: List[Event],
        service_statuses: List[ServiceStatus],
        overall_status: str,
    ) -> Dict[str, Any]:
        """Return a full Discord message payload (with 'embeds' key)."""

        color = {
            "healthy": _COLOR_HEALTHY,
            "warning": _COLOR_WARNING,
            "critical": _COLOR_CRITICAL,
        }.get(overall_status, _COLOR_UNKNOWN)

        hostname = socket.gethostname()
        status_icon = _STATUS_ICONS.get(overall_status, "❔")
        status_label = _STATUS_LABELS.get(overall_status, overall_status.title())

        fields: List[Dict] = []

        if metrics:
            fields += self._metric_fields(metrics)

        if service_statuses:
            fields.append(self._service_field(service_statuses))

        if events:
            fields.append(self._event_field(events))
        else:
            fields.append({
                "name": "📋 Ereignisse",
                "value": "_Keine aktuellen Ereignisse_",
                "inline": False,
            })

        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        embed: Dict[str, Any] = {
            "title": f"🖥️ {hostname}  │  {status_icon} {status_label}",
            "color": color,
            "fields": fields,
            "footer": {"text": f"🔄 Zuletzt aktualisiert: {now_str}"},
        }

        return {"embeds": [embed]}

    # ------------------------------------------------------------------
    # Metric fields
    # ------------------------------------------------------------------

    def _metric_fields(self, m: SystemMetrics) -> List[Dict]:
        fields = []

        # CPU
        fields.append({
            "name": "💻 CPU",
            "value": f"**{m.cpu_percent}%**",
            "inline": True,
        })

        # RAM
        fields.append({
            "name": "🧠 RAM",
            "value": f"**{m.ram_percent}%**\n{m.ram_used_gb:.1f} / {m.ram_total_gb:.1f} GB",
            "inline": True,
        })

        # Swap
        if m.swap_total_gb > 0:
            fields.append({
                "name": "🔄 Swap",
                "value": f"**{m.swap_percent}%**\n{m.swap_used_gb:.1f} / {m.swap_total_gb:.1f} GB",
                "inline": True,
            })

        # Disks (up to 4)
        for disk in m.disks[:4]:
            emoji = "🔴" if disk.percent >= self._cfg.thresholds.disk_percent else "📂"
            fields.append({
                "name": f"{emoji} Disk `{disk.mountpoint}`",
                "value": f"**{disk.percent}%**\n{disk.used_gb:.0f} / {disk.total_gb:.0f} GB",
                "inline": True,
            })

        # I/O Wait
        fields.append({
            "name": "⚡ I/O Wait",
            "value": f"**{m.iowait_percent}%**",
            "inline": True,
        })

        # Load average
        la = m.load_avg
        fields.append({
            "name": "📊 Load Avg",
            "value": f"`{la[0]} / {la[1]} / {la[2]}`\n1m / 5m / 15m",
            "inline": True,
        })

        # Network
        if self._cfg.network.enabled:
            fields.append({
                "name": f"🌐 Netz `{self._cfg.network.interface}`",
                "value": f"↑ {m.net_mbits_sent} Mbit/s\n↓ {m.net_mbits_recv} Mbit/s",
                "inline": True,
            })

        # Uptime
        fields.append({
            "name": "⏱️ Uptime",
            "value": format_uptime(m.uptime_seconds),
            "inline": True,
        })

        # Visual separator
        fields.append({
            "name": "\u200b",   # zero-width space
            "value": "\u200b",
            "inline": False,
        })

        return fields

    # ------------------------------------------------------------------
    # Service field
    # ------------------------------------------------------------------

    def _service_field(self, statuses: List[ServiceStatus]) -> Dict:
        lines = []
        for s in statuses:
            if s.active_state == "active":
                icon = "✅"
            elif s.severity == "critical":
                icon = "🚨"
            elif s.severity == "warning":
                icon = "⚠️"
            else:
                icon = "❔"
            sub = s.sub_state if s.sub_state != "unknown" else s.active_state
            lines.append(f"{icon} `{s.name}` — {sub}")

        value = "\n".join(lines) or "_Keine Services konfiguriert_"
        return {
            "name": "🔧 Services",
            "value": truncate(value, 1020),
            "inline": False,
        }

    # ------------------------------------------------------------------
    # Event field
    # ------------------------------------------------------------------

    def _event_field(self, events: List[Event]) -> Dict:
        lines = []
        for e in events:
            ts = datetime.fromtimestamp(e.timestamp).strftime("%d.%m %H:%M")
            emoji = severity_emoji(e.severity)
            title = truncate(e.title, 80)
            lines.append(f"`{ts}` {emoji} {title}")

        value = "\n".join(lines) or "_Keine Ereignisse_"
        return {
            "name": f"📋 Ereignisse (letzte {len(events)})",
            "value": truncate(value, 1020),
            "inline": False,
        }
