"""Journalctl/journald integration for error and OOM event extraction."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Severity map from syslog priority numbers
_PRIORITY_MAP = {
    "0": "critical", "1": "critical", "2": "critical",
    "3": "error",
    "4": "warning",
    "5": "info", "6": "info", "7": "info",
}

# OOM kill pattern
_OOM_RE = re.compile(r"(Out of memory:|oom_kill_process|oom-kill:)", re.IGNORECASE)
_OOM_PROCESS_RE = re.compile(r"Killed process (\d+) \((.+?)\)", re.IGNORECASE)


class JournalEntry:
    __slots__ = ("timestamp", "unit", "message", "priority", "pid")

    def __init__(self, timestamp: float, unit: str, message: str, priority: str, pid: str = ""):
        self.timestamp = timestamp
        self.unit = unit
        self.message = message
        self.priority = priority
        self.pid = pid


class JournalCollector:
    """Reads recent journal entries via journalctl subprocess."""

    def __init__(self, config) -> None:
        self._cfg = config.journal
        self._available = self._check_available()

    # ------------------------------------------------------------------

    def _check_available(self) -> bool:
        try:
            result = subprocess.run(
                ["journalctl", "--version"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.warning("journalctl not found – journal monitoring disabled")
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------

    def collect_recent(self, is_startup: bool = False) -> Tuple[List[JournalEntry], List[JournalEntry]]:
        """
        Return (error_entries, oom_entries) from the last N minutes.
        On startup, only entries >= lookback_min_priority are returned (if lookback_enabled).
        During normal polling, uses a short 1-cycle window.
        """
        if not self._cfg.enabled or not self._available:
            return [], []

        if is_startup:
            if not self._cfg.lookback_enabled:
                return [], []
            since = f"{self._cfg.lookback_minutes} minutes ago"
            # Override priority to lookback minimum on startup
            priority_arg = self._priority_number(self._cfg.lookback_min_priority)
        else:
            # Normal poll: only look back the collect interval window
            since = "5 minutes ago"
            priority_arg = self._build_priority_filter()

        cmd = [
            "journalctl",
            f"--since={since}",
            f"--priority={priority_arg}",
            "--output=json",
            "--no-pager",
            "--quiet",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode not in (0, 1):
                logger.debug("journalctl returned %d: %s", result.returncode, result.stderr[:200])
                return [], []
        except subprocess.TimeoutExpired:
            logger.warning("journalctl timed out")
            return [], []
        except Exception as exc:
            logger.error("Failed to run journalctl: %s", exc)
            return [], []

        entries, ooms = self._parse_output(result.stdout)

        # On startup: additionally filter by lookback_min_priority level
        if is_startup:
            min_level = self._severity_level(self._cfg.lookback_min_priority)
            entries = [e for e in entries if self._severity_level(e.priority) >= min_level]
            ooms    = [o for o in ooms    if self._severity_level(o.priority) >= min_level]

        return entries, ooms

    # ------------------------------------------------------------------

    def _build_priority_filter(self) -> str:
        """Build the journalctl --priority argument from configured list."""
        nums = [n for p in self._cfg.priorities if (n := self._PRIO_NUMBERS.get(p.lower()))]
        return max(nums) if nums else "3"

    def _priority_number(self, name: str) -> str:
        """Convert severity name (error/warning/critical/info) to journalctl priority number."""
        _map = {
            "critical": "2",   # crit and above
            "error":    "3",   # err and above
            "warning":  "4",   # warning and above
            "info":     "6",   # info and above
        }
        return _map.get(name.lower(), "3")

    @staticmethod
    def _severity_level(name: str) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}.get(name.lower(), 0)

    _PRIO_NUMBERS = {
        "emerg": "0", "alert": "1", "crit": "2", "err": "3",
        "warning": "4", "notice": "5", "info": "6", "debug": "7",
    }

    # ------------------------------------------------------------------

    def _parse_output(self, output: str) -> Tuple[List[JournalEntry], List[JournalEntry]]:
        entries: List[JournalEntry] = []
        ooms: List[JournalEntry] = []

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            message = obj.get("MESSAGE", "")
            if isinstance(message, list):
                # Binary message – skip
                continue

            unit = obj.get("_SYSTEMD_UNIT", obj.get("SYSLOG_IDENTIFIER", "kernel"))
            priority_raw = obj.get("PRIORITY", "6")
            pid = obj.get("_PID", "")

            # Timestamp: journal uses microseconds since epoch
            ts_us = obj.get("__REALTIME_TIMESTAMP")
            if ts_us:
                try:
                    ts = int(ts_us) / 1_000_000
                except (ValueError, TypeError):
                    ts = time.time()
            else:
                ts = time.time()

            # Apply unit filters
            if self._cfg.include_units and unit not in self._cfg.include_units:
                continue
            if unit in self._cfg.exclude_units:
                continue

            severity = _PRIORITY_MAP.get(str(priority_raw), "info")
            entry = JournalEntry(
                timestamp=ts,
                unit=unit,
                message=str(message)[:300],
                priority=severity,
                pid=str(pid),
            )

            # OOM detection
            if self._cfg.oom_detection and _OOM_RE.search(message):
                ooms.append(entry)
            else:
                entries.append(entry)

        return entries, ooms

    # ------------------------------------------------------------------

    def format_event_key(self, entry: JournalEntry) -> str:
        """Stable deduplication key for a journal entry."""
        # Key on unit + first 60 chars of message to avoid per-second spam
        msg_fragment = re.sub(r"\d+", "#", entry.message[:60])
        return f"journal:{entry.unit}:{msg_fragment}"

    def format_title(self, entry: JournalEntry) -> str:
        unit_short = entry.unit.replace(".service", "").replace(".timer", "")
        msg = entry.message[:80]
        return f"[{unit_short}] {msg}"
