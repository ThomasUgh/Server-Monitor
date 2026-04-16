"""External health checks: HTTP endpoints, TCP ports, TLS certificate expiry."""
from __future__ import annotations

import logging
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class HttpCheckResult:
    url: str
    name: str
    ok: bool
    status_code: Optional[int]
    response_ms: Optional[float]
    error: str = ""


@dataclass
class PortCheckResult:
    host: str
    port: int
    name: str
    ok: bool
    response_ms: Optional[float]
    error: str = ""


@dataclass
class CertCheckResult:
    host: str
    port: int
    ok: bool
    days_remaining: Optional[int]
    expiry_str: str = ""
    error: str = ""


class ExternalChecker:
    """Run HTTP, port, and certificate checks."""

    def __init__(self, config) -> None:
        self._http_checks = config.http_checks
        self._port_checks = config.port_checks
        self._cert_checks = config.cert_checks

    # ------------------------------------------------------------------
    # HTTP Checks
    # ------------------------------------------------------------------

    def run_http_checks(self) -> List[HttpCheckResult]:
        results = []
        for chk in self._http_checks:
            results.append(self._check_http(chk))
        return results

    def _check_http(self, chk) -> HttpCheckResult:
        t0 = time.monotonic()
        try:
            resp = requests.get(
                chk.url,
                timeout=chk.timeout,
                allow_redirects=True,
                headers={"User-Agent": "server-monitor/1.0"},
            )
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            ok = resp.status_code == chk.expected_status
            return HttpCheckResult(
                url=chk.url,
                name=chk.name or chk.url,
                ok=ok,
                status_code=resp.status_code,
                response_ms=elapsed_ms,
                error="" if ok else f"HTTP {resp.status_code} (erwartet {chk.expected_status})",
            )
        except requests.exceptions.ConnectionError as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            return HttpCheckResult(
                url=chk.url, name=chk.name or chk.url,
                ok=False, status_code=None, response_ms=elapsed_ms,
                error=f"Verbindungsfehler: {_short_exc(exc)}",
            )
        except requests.exceptions.Timeout:
            return HttpCheckResult(
                url=chk.url, name=chk.name or chk.url,
                ok=False, status_code=None, response_ms=None,
                error=f"Timeout nach {chk.timeout}s",
            )
        except Exception as exc:
            return HttpCheckResult(
                url=chk.url, name=chk.name or chk.url,
                ok=False, status_code=None, response_ms=None,
                error=_short_exc(exc),
            )

    # ------------------------------------------------------------------
    # Port Checks
    # ------------------------------------------------------------------

    def run_port_checks(self) -> List[PortCheckResult]:
        results = []
        for chk in self._port_checks:
            results.append(self._check_port(chk))
        return results

    def _check_port(self, chk) -> PortCheckResult:
        name = chk.name or f"{chk.host}:{chk.port}"
        t0 = time.monotonic()
        try:
            with socket.create_connection((chk.host, chk.port), timeout=chk.timeout):
                elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            return PortCheckResult(
                host=chk.host, port=chk.port, name=name,
                ok=True, response_ms=elapsed_ms,
            )
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            return PortCheckResult(
                host=chk.host, port=chk.port, name=name,
                ok=False, response_ms=None,
                error=_short_exc(exc),
            )

    # ------------------------------------------------------------------
    # Certificate Checks
    # ------------------------------------------------------------------

    def run_cert_checks(self) -> List[CertCheckResult]:
        results = []
        for chk in self._cert_checks:
            results.append(self._check_cert(chk))
        return results

    def _check_cert(self, chk) -> CertCheckResult:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((chk.host, chk.port), timeout=10) as raw:
                with ctx.wrap_socket(raw, server_hostname=chk.host) as ssock:
                    cert = ssock.getpeercert()

            not_after = cert.get("notAfter", "")
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
            now = datetime.now(timezone.utc)
            days_remaining = (expiry - now).days

            return CertCheckResult(
                host=chk.host, port=chk.port,
                ok=days_remaining > 0,
                days_remaining=days_remaining,
                expiry_str=expiry.strftime("%d.%m.%Y"),
            )
        except ssl.SSLCertVerificationError as exc:
            return CertCheckResult(
                host=chk.host, port=chk.port,
                ok=False, days_remaining=None,
                error=f"Zertifikat ungültig: {_short_exc(exc)}",
            )
        except Exception as exc:
            return CertCheckResult(
                host=chk.host, port=chk.port,
                ok=False, days_remaining=None,
                error=_short_exc(exc),
            )


def _short_exc(exc: Exception) -> str:
    msg = str(exc)
    return msg[:120] if msg else type(exc).__name__
