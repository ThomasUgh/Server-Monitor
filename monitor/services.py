"""Systemd service status monitoring via systemctl subprocess."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceStatus:
    name: str
    active_state: str       # active | inactive | failed | activating | deactivating
    sub_state: str          # running | dead | failed | ...
    load_state: str         # loaded | not-found | masked
    ok: bool
    severity: str           # ok | warning | critical


class ServiceChecker:
    """Check systemd unit status using systemctl."""

    def __init__(self, config) -> None:
        self._services = config.monitored_services
        self._available = self._check_available()

    def _check_available(self) -> bool:
        try:
            r = subprocess.run(["systemctl", "--version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except FileNotFoundError:
            logger.warning("systemctl not found – service monitoring disabled")
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------

    def check_all(self) -> List[ServiceStatus]:
        """Return status for all configured services."""
        if not self._available or not self._services:
            return []

        results = []
        for svc_cfg in self._services:
            status = self._check_one(svc_cfg.name)
            if status is None:
                continue

            # Determine severity
            if status.active_state == "failed" or status.sub_state == "failed":
                severity = "critical" if svc_cfg.critical_on_failed else "warning"
                ok = False
            elif status.active_state == "inactive" and status.load_state == "loaded":
                if svc_cfg.warn_on_inactive:
                    severity = "warning"
                    ok = False
                else:
                    severity = "ok"
                    ok = True
            elif status.load_state in ("not-found", "masked"):
                severity = "warning"
                ok = False
            elif status.active_state == "active":
                severity = "ok"
                ok = True
            else:
                severity = "ok"
                ok = True

            status.ok = ok
            status.severity = severity
            results.append(status)

        return results

    # ------------------------------------------------------------------

    def _check_one(self, name: str) -> Optional[ServiceStatus]:
        # Ensure .service suffix for cleaner matching
        unit = name if "." in name else f"{name}.service"
        try:
            result = subprocess.run(
                [
                    "systemctl", "show", unit,
                    "--property=ActiveState,SubState,LoadState",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            logger.warning("systemctl timed out for unit %s", unit)
            return None
        except Exception as exc:
            logger.error("systemctl failed for %s: %s", unit, exc)
            return None

        props: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()

        return ServiceStatus(
            name=name,
            active_state=props.get("ActiveState", "unknown"),
            sub_state=props.get("SubState", "unknown"),
            load_state=props.get("LoadState", "unknown"),
            ok=True,        # will be overwritten by caller
            severity="ok",  # will be overwritten by caller
        )
