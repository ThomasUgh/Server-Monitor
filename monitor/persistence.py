"""Persistent state management (message ID, boot time, events, cooldowns)."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StateManager:
    """Reads and writes monitor state to a JSON file on disk."""

    _DEFAULTS: Dict[str, Any] = {
        "message_id": None,
        "last_boot_time": None,
        "last_run_time": None,
        "events": [],           # list of serialised Event dicts
        "cooldowns": {},        # event_key -> last_fired timestamp
    }

    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Merge defaults for any missing keys
                for k, v in self._DEFAULTS.items():
                    data.setdefault(k, v)
                return data
            except Exception as exc:
                logger.warning("Could not load state file (%s) – starting fresh: %s", self._path, exc)
        return dict(self._DEFAULTS)

    def _save(self) -> None:
        try:
            tmp = str(self._path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            logger.error("Failed to save state: %s", exc)

    # ------------------------------------------------------------------
    # message_id
    # ------------------------------------------------------------------

    @property
    def message_id(self) -> Optional[str]:
        return self._data.get("message_id")

    @message_id.setter
    def message_id(self, value: Optional[str]) -> None:
        self._data["message_id"] = value
        self._save()

    # ------------------------------------------------------------------
    # Boot / run tracking
    # ------------------------------------------------------------------

    @property
    def last_boot_time(self) -> Optional[float]:
        return self._data.get("last_boot_time")

    @last_boot_time.setter
    def last_boot_time(self, value: float) -> None:
        self._data["last_boot_time"] = value
        self._save()

    @property
    def last_run_time(self) -> Optional[float]:
        return self._data.get("last_run_time")

    def update_run_time(self) -> None:
        self._data["last_run_time"] = time.time()
        self._save()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_events(self) -> List[Dict]:
        return list(self._data.get("events", []))

    def set_events(self, events: List[Dict]) -> None:
        self._data["events"] = events
        self._save()

    # ------------------------------------------------------------------
    # Cooldowns
    # ------------------------------------------------------------------

    def get_cooldown(self, key: str) -> Optional[float]:
        """Return timestamp of last fire for *key*, or None."""
        return self._data["cooldowns"].get(key)

    def set_cooldown(self, key: str, ts: Optional[float] = None) -> None:
        self._data["cooldowns"][key] = ts if ts is not None else time.time()
        self._save()

    def is_on_cooldown(self, key: str, cooldown_minutes: float) -> bool:
        last = self.get_cooldown(key)
        if last is None:
            return False
        return (time.time() - last) < (cooldown_minutes * 60)

    def clear_cooldown(self, key: str) -> None:
        self._data["cooldowns"].pop(key, None)
        self._save()

    # ------------------------------------------------------------------
    # Bulk save helper
    # ------------------------------------------------------------------

    def flush(self) -> None:
        self._save()
