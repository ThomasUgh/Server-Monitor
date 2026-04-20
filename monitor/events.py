"""Event management: creation, deduplication, severity filtering, TTL expiry."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
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
    """Central event queue with deduplication, severity filtering and TTL expiry."""

    def __init__(self, config) -> None:
        self._config = config
        self._min_level: int = severity_level(config.severity_mode)
        self._events: List[Event] = []
        self.pending_immediate: bool = False
        self._immediate_level: int = severity_level(config.immediate_update_severity)

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    def load_from_state(self, raw: List[dict]) -> None:
        seen_keys: dict = {}
        for d in raw:
            try:
                e = Event.from_dict(d)
                if self._is_expired(e):
                    continue
                # Keep only the newest event per key
                if e.key not in seen_keys or e.timestamp > seen_keys[e.key].timestamp:
                    seen_keys[e.key] = e
            except Exception:
                pass
        self._events = sorted(seen_keys.values(), key=lambda e: e.timestamp, reverse=True)

    def to_state(self) -> List[dict]:
        return [e.to_dict() for e in self._events]

    # ------------------------------------------------------------------
    # Adding events
    # ------------------------------------------------------------------

    def add(self, event: Event) -> bool:
        """Add event. Replaces any existing event with the same key (no duplicates)."""
        if event.level < self._min_level:
            return False

        # Remove existing event with same key to avoid duplicates
        self._events = [e for e in self._events if e.key != event.key]

        self._events.append(event)
        self._events.sort(key=lambda e: e.timestamp, reverse=True)

        max_buf = max(self._config.max_events_displayed * 3, 50)
        if len(self._events) > max_buf:
            self._events = self._events[:max_buf]

        if event.level >= self._immediate_level:
            self.pending_immediate = True

        return True

    def remove_by_key_prefix(self, prefix: str) -> int:
        """Remove all events whose key starts with *prefix*. Returns count removed."""
        before = len(self._events)
        self._events = [e for e in self._events if not e.key.startswith(prefix)]
        return before - len(self._events)

    def remove_by_key(self, key: str) -> bool:
        """Remove a specific event by exact key. Returns True if found."""
        before = len(self._events)
        self._events = [e for e in self._events if e.key != key]
        return len(self._events) < before

    # ------------------------------------------------------------------
    # TTL expiry
    # ------------------------------------------------------------------

    def _is_expired(self, event: Event) -> bool:
        ttl = getattr(self._config, "event_ttl_minutes", 120)
        if ttl <= 0:
            return False  # TTL disabled
        return (time.time() - event.timestamp) > ttl * 60

    def expire_old_events(self) -> int:
        """Remove expired events and deduplicate by key. Returns count removed."""
        before = len(self._events)
        # Remove expired
        live = [e for e in self._events if not self._is_expired(e)]
        # Deduplicate: keep newest per key
        seen: dict = {}
        for e in live:
            if e.key not in seen or e.timestamp > seen[e.key].timestamp:
                seen[e.key] = e
        self._events = sorted(seen.values(), key=lambda e: e.timestamp, reverse=True)
        return before - len(self._events)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_display_events(self) -> List[Event]:
        """Return the most recent N non-expired events for embed display."""
        live = [e for e in self._events if not self._is_expired(e)]
        return live[: self._config.max_events_displayed]

    def clear_pending_immediate(self) -> None:
        self.pending_immediate = False

    def overall_status(self) -> str:
        """Derive overall status from recent non-expired events."""
        cutoff = time.time() - 30 * 60
        recent = [e for e in self._events
                  if e.timestamp >= cutoff and not self._is_expired(e)]
        if not recent:
            return "healthy"
        max_level = max(e.level for e in recent)
        if max_level >= severity_level("critical"):
            return "critical"
        if max_level >= severity_level("warning"):
            return "warning"
        return "healthy"
