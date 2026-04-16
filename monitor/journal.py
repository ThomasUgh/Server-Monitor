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


@dataclass_compat := None  # just to avoid importing dataclass here


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

    def collect_recent(self) -> Tuple[List[JournalEntry], List[JournalEntry]]:
        """
        Return (error_entries, oom_entries) from the last N minutes.
        """
        if not self._cfg.enabled or not self._available:
            return [], []

        since = f"{self._cfg.lookback_minutes} minutes ago"
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
        return entries, ooms

    # ------------------------------------------------------------------

    def _build_priority_filter(self) -> str:
        """Build the journalctl --priority argument from configured list."""
        _prio_numbers = {
            "emerg": "0", "alert": "1", "crit": "2", "err": "3",
            "warning": "4", "notice": "5", "info": "6", "debug": "7",
        }
        nums = []
        for p in self._cfg.priorities:
            n = _prio_numbers.get(p.lower())
            if n:
                nums.append(n)
        if not nums:
            return "3"
        # journalctl accepts highest priority number (most verbose) in the range
        return max(nums)

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
