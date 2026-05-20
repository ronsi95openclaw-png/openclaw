"""Encrypted local secret management — never logs secret values."""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.security.secrets")


class SecretsManager:
    """Manages encrypted local secrets.

    Uses environment variables as primary source.
    Falls back to encrypted file store.
    NEVER logs secret values.
    """

    _SENSITIVE_KEYS = {
        "BLOFIN_API_KEY",
        "BLOFIN_API_SECRET",
        "BLOFIN_PASSPHRASE",
        "TELEGRAM_BOT_TOKEN",
        "DB_PASSWORD",
        "DASHBOARD_TOKEN",
    }

    def __init__(self, secrets_file: str = "data/secrets.enc") -> None:
        self._file = Path(secrets_file)
        self._cache: dict = {}
        self._loaded = False

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret by key. Env vars take priority over file store."""
        # 1. Try environment
        val = os.environ.get(key)
        if val is not None:
            return val
        # 2. Try file store
        if not self._loaded:
            self._load()
        return self._cache.get(key, default)

    def _load(self) -> None:
        """Load from encrypted file store. Uses simple XOR + base64 (not production-grade)."""
        self._loaded = True
        if not self._file.exists():
            return
        try:
            raw = self._file.read_bytes()
            decoded = base64.b64decode(raw)
            # Simple byte rotation (placeholder — real impl would use cryptography lib)
            plain = bytes(b ^ 0x42 for b in decoded)
            self._cache = json.loads(plain.decode())
        except Exception as exc:
            logger.error(
                "Failed to load secrets file (redacted): %s", type(exc).__name__
            )

    def is_sensitive(self, key: str) -> bool:
        return key.upper() in self._SENSITIVE_KEYS

    def audit_log(self, key: str, accessor: str) -> None:
        """Log secret access audit (key name only, never value)."""
        logger.info(
            "SECRET_ACCESS key=%s accessor=%s",
            key if not self.is_sensitive(key) else "***REDACTED***",
            accessor,
        )
