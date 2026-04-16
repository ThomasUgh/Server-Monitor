"""Discord webhook client: create, edit and recover the persistent embed message."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from monitor.persistence import StateManager

logger = logging.getLogger(__name__)

_DISCORD_API = "https://discord.com/api/v10"

# Rate-limit / retry settings
_MAX_RETRIES = 5
_BASE_BACKOFF = 2.0          # seconds
_MAX_BACKOFF = 120.0


class DiscordNotifier:
    """
    Manages the single persistent Discord embed message.
    On first run: POST to webhook → save message_id.
    On subsequent runs: PATCH the existing message.
    On failure to PATCH (message deleted, etc.): re-create.
    """

    def __init__(self, config, state: StateManager) -> None:
        self._webhook_url = config.discord_webhook_url.rstrip("/")
        self._state = state
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "server-monitor/1.0"})
        # Extract webhook ID+token for message management endpoints
        parsed = urlparse(self._webhook_url)
        parts = parsed.path.split("/")
        # URL format: /api/webhooks/{id}/{token}
        try:
            idx = parts.index("webhooks")
            self._webhook_id = parts[idx + 1]
            self._webhook_token = parts[idx + 2]
        except (ValueError, IndexError):
            self._webhook_id = None
            self._webhook_token = None
            logger.warning("Could not parse webhook ID/token from URL – message editing may fail")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def send_or_update(self, payload: Dict[str, Any]) -> bool:
        """
        Send or update the persistent embed.
        Returns True on success.
        """
        message_id = self._state.message_id

        if message_id:
            success = self._edit_message(message_id, payload)
            if success:
                return True
            logger.warning("Could not edit message %s – attempting to re-create", message_id)
            # Try to delete the old message (best effort)
            self._delete_message(message_id)
            self._state.message_id = None

        # Create new message
        new_id = self._post_message(payload)
        if new_id:
            self._state.message_id = new_id
            logger.info("Created new Discord message: %s", new_id)
            return True

        logger.error("Failed to create Discord message after retries")
        return False

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _post_message(self, payload: Dict) -> Optional[str]:
        """POST to webhook with ?wait=true to get message_id back."""
        url = f"{self._webhook_url}?wait=true"
        response = self._request_with_retry("POST", url, json=payload)
        if response and response.status_code in (200, 204):
            try:
                data = response.json()
                return str(data["id"])
            except Exception as exc:
                logger.error("Could not parse message ID from response: %s", exc)
        return None

    def _edit_message(self, message_id: str, payload: Dict) -> bool:
        """PATCH the existing webhook message."""
        if not self._webhook_id or not self._webhook_token:
            logger.warning("Cannot edit message – webhook ID/token unknown")
            return False
        url = (
            f"{_DISCORD_API}/webhooks/{self._webhook_id}/{self._webhook_token}"
            f"/messages/{message_id}"
        )
        response = self._request_with_retry("PATCH", url, json=payload)
        if response and response.status_code in (200, 204):
            return True
        if response:
            logger.warning("Edit message returned HTTP %d", response.status_code)
            if response.status_code == 404:
                return False  # message gone
        return False

    def _delete_message(self, message_id: str) -> None:
        """DELETE the webhook message (best-effort, no retry)."""
        if not self._webhook_id or not self._webhook_token:
            return
        url = (
            f"{_DISCORD_API}/webhooks/{self._webhook_id}/{self._webhook_token}"
            f"/messages/{message_id}"
        )
        try:
            self._session.delete(url, timeout=10)
        except Exception:
            pass

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Optional[requests.Response]:
        backoff = _BASE_BACKOFF
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.request(method, url, timeout=15, **kwargs)

                # Discord rate limit
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    logger.warning("Discord rate limit hit – waiting %.1fs", retry_after)
                    time.sleep(min(retry_after + 0.5, _MAX_BACKOFF))
                    continue

                # 5xx server errors – retry
                if resp.status_code >= 500:
                    logger.warning(
                        "Discord server error %d (attempt %d/%d)",
                        resp.status_code, attempt, _MAX_RETRIES,
                    )
                    time.sleep(min(backoff, _MAX_BACKOFF))
                    backoff *= 2
                    continue

                # Non-retryable error or success
                return resp

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                logger.warning(
                    "Discord request failed (attempt %d/%d): %s",
                    attempt, _MAX_RETRIES, exc,
                )
                time.sleep(min(backoff, _MAX_BACKOFF))
                backoff *= 2

        logger.error("All %d Discord request attempts exhausted", _MAX_RETRIES)
        return None
