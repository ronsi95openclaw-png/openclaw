"""Authentication utilities for dashboard and Telegram bot."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger("openclaw.security.auth")


class TokenAuth:
    """Simple token-based authentication for dashboard endpoints."""

    def __init__(self, token: Optional[str] = None) -> None:
        resolved = token or os.environ.get("DASHBOARD_TOKEN", "")
        if not resolved:
            logger.warning(
                "DASHBOARD_TOKEN is not set — dashboard API is UNAUTHENTICATED. "
                "Set DASHBOARD_TOKEN in .env before any network exposure."
            )
            self._token = ""
        elif resolved == "changeme":
            logger.warning(
                "DASHBOARD_TOKEN is still the default 'changeme' — change it in .env "
                "before any network exposure."
            )
            self._token = resolved
        else:
            self._token = resolved

    def verify_token(self, provided: str) -> bool:
        """Constant-time comparison to prevent timing attacks."""
        if not self._token:
            return True   # unauthenticated mode — allow all from local only
        return hmac.compare_digest(
            self._token.encode(),
            provided.encode() if provided else b"",
        )

    def is_local_request(self, remote_addr: str) -> bool:
        return remote_addr in ("127.0.0.1", "::1", "localhost")


class TelegramAuthChecker:
    """Validates Telegram user IDs against allowlist."""

    def __init__(self, allowed_ids: Optional[list] = None) -> None:
        self._allowed: set = set(allowed_ids or [])
        # Also load from env: TELEGRAM_ALLOWED_IDS=123,456,789
        env_ids = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
        for uid in env_ids.split(","):
            uid = uid.strip()
            if uid.isdigit():
                self._allowed.add(int(uid))

    def is_allowed(self, user_id: int) -> bool:
        if not self._allowed:
            # No allowlist configured — deny by default and warn operator.
            # Set TELEGRAM_ALLOWED_IDS in .env to allow your user ID.
            logger.warning(
                "TelegramAuthChecker: no allowlist configured — denying user_id=%s. "
                "Set TELEGRAM_ALLOWED_IDS in .env to enable Telegram commands.",
                user_id,
            )
            return False
        return user_id in self._allowed
