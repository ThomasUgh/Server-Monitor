"""Utility helpers: logging setup, formatting, small conversions."""
import logging
import logging.handlers
import os
from pathlib import Path
from datetime import timedelta


def setup_logging(log_file: str, level: str = "INFO") -> None:
    """Configure root logger to write to file and stdout."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s – %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Rotating file handler (5 MB × 3 backups)
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


def format_bytes(n: float) -> str:
    """Human-readable bytes (GB/MB/KB)."""
    for unit in ("TB", "GB", "MB", "KB"):
        divisor = {"TB": 1e12, "GB": 1e9, "MB": 1e6, "KB": 1e3}[unit]
        if n >= divisor:
            return f"{n / divisor:.1f} {unit}"
    return f"{int(n)} B"


def format_uptime(seconds: float) -> str:
    """Return human-readable uptime string."""
    td = timedelta(seconds=int(seconds))
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def severity_level(name: str) -> int:
    """Convert severity name to numeric level."""
    return {"info": 0, "warning": 1, "error": 2, "critical": 3}.get(name.lower(), 0)


def severity_emoji(name: str) -> str:
    return {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "🔴",
        "critical": "🚨",
    }.get(name.lower(), "❔")


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "…"
