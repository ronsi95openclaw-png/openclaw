"""Authentication utilities for dashboard and Telegram bot."""
from __future__ import annotations

import hmac
import os
from typing import Optional


class TokenAuth:
    """Simple token-based authentication for dashboard endpoints."""

    def __init__(self, token: Optional[str] = None) -> None:
        self._token = token or os.environ.get("DASHBOARD_TOKEN", "changeme")

    def verify_token(self, provided: str) -> bool:
        """Constant-time comparison to prevent timing attacks."""
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
            return True  # No allowlist = allow all (dev mode)
        return user_id in self._allowed
