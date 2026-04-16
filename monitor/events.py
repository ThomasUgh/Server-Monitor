"""Event management: creation, deduplication, severity filtering, storage."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from monitor.utils import severity_level


@dataclass
class Event:
    timestamp: float
    severity: str       # info | warning | error | critical
    key: str            # deduplication key
    title: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Event":
        return Event(
            timestamp=d["timestamp"],
            severity=d["severity"],
            key=d["key"],
            title=d["title"],
            description=d.get("description", ""),
        )

    @property
    def level(self) -> int:
        return severity_level(self.severity)


class EventManager:
    """Central event queue with deduplication and severity filtering."""

    def __init__(self, config) -> None:
        self._config = config
        self._min_level: int = severity_level(config.severity_mode)
        self._events: List[Event] = []
        # pending_immediate: True if an event since last Discord update needs
        # an immediate push (based on immediate_update_severity).
        self.pending_immediate: bool = False
        self._immediate_level: int = severity_level(config.immediate_update_severity)

    # ------------------------------------------------------------------
    # Loading / saving from state
    # ------------------------------------------------------------------

    def load_from_state(self, raw: List[dict]) -> None:
        self._events = []
        for d in raw:
            try:
                self._events.append(Event.from_dict(d))
            except Exception:
                pass

    def to_state(self) -> List[dict]:
        return [e.to_dict() for e in self._events]

    # ------------------------------------------------------------------
    # Adding events
    # ------------------------------------------------------------------

    def add(self, event: Event) -> bool:
        """
        Add an event to the queue.
        Returns True if the event was accepted (passes severity filter).
        The caller is responsible for cooldown checks via StateManager.
        """
        if event.level < self._min_level:
            return False

        self._events.append(event)
        self._events.sort(key=lambda e: e.timestamp, reverse=True)

        # Trim to a reasonable internal buffer (3× display max to handle filtering)
        max_buf = max(self._config.max_events_displayed * 3, 50)
        if len(self._events) > max_buf:
            self._events = self._events[:max_buf]

        # Mark for immediate Discord update?
        if event.level >= self._immediate_level:
            self.pending_immediate = True

        return True

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_display_events(self) -> List[Event]:
        """Return the most recent N events for embed display."""
        return self._events[: self._config.max_events_displayed]

    def clear_pending_immediate(self) -> None:
        self.pending_immediate = False

    def overall_status(self) -> str:
        """
        Derive overall server status from recent events.
        Uses only events from the last 30 minutes for status determination.
        """
        cutoff = time.time() - 30 * 60
        recent = [e for e in self._events if e.timestamp >= cutoff]
        if not recent:
            return "healthy"
        max_level = max(e.level for e in recent)
        if max_level >= severity_level("critical"):
            return "critical"
        if max_level >= severity_level("warning"):
            return "warning"
        return "healthy"
