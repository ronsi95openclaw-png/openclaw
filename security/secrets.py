"""Encrypted local secret management — never logs secret values."""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.security.secrets")

_DEFAULT_SECRETS_FILE = "data/secrets.enc"
_DEFAULT_KEY_FILE     = "data/secrets.key"


def _get_fernet():
    from cryptography.fernet import Fernet
    return Fernet


class SecretsManager:
    """Manages encrypted local secrets.

    Uses environment variables as primary source.
    Falls back to Fernet-encrypted file store (key in data/secrets.key or
    SECRETS_KEY_FILE env var). If an existing file uses the legacy XOR format,
    a CRITICAL warning is logged; re-save with store() to upgrade.

    NEVER logs secret values.
    """

    _SENSITIVE_KEYS = {
        "BLOFIN_API_KEY",
        "BLOFIN_API_SECRET",
        "BLOFIN_PASSPHRASE",
        "TELEGRAM_BOT_TOKEN",
        "DB_PASSWORD",
        "DASHBOARD_TOKEN",
        "CRYPTOCOM_API_KEY",
        "CRYPTOCOM_SECRET",
    }

    def __init__(self, secrets_file: str = _DEFAULT_SECRETS_FILE,
                 key_file: str = _DEFAULT_KEY_FILE) -> None:
        self._file     = Path(secrets_file)
        self._key_file = Path(os.environ.get("SECRETS_KEY_FILE", key_file))
        self._cache: dict = {}
        self._loaded = False

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret by key. Env vars take priority over file store."""
        val = os.environ.get(key)
        if val is not None:
            return val
        if not self._loaded:
            self._load()
        return self._cache.get(key, default)

    def store(self, secrets: dict) -> None:
        """Write secrets dict to Fernet-encrypted file.

        Generates a new key if none exists yet. The key is stored separately
        from the secrets file — protect data/secrets.key with filesystem ACLs.
        """
        Fernet = _get_fernet()
        key = self._load_or_create_key()
        f   = Fernet(key)
        plaintext = json.dumps(secrets).encode("utf-8")
        ciphertext = f.encrypt(plaintext)
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_bytes(ciphertext)
        self._cache  = dict(secrets)
        self._loaded = True
        logger.info("SecretsManager: wrote %d secrets (Fernet-encrypted)", len(secrets))

    def _load(self) -> None:
        """Load from encrypted file store, preferring Fernet over legacy XOR."""
        self._loaded = True
        if not self._file.exists():
            return
        raw = self._file.read_bytes()
        if self._try_fernet(raw):
            return
        # Fall back to legacy XOR — never acceptable for live API keys.
        logger.critical(
            "SecretsManager: secrets file uses LEGACY XOR encoding — "
            "this is trivially reversible and MUST be re-encrypted with "
            "store() before deploying live API keys. "
            "Run: python -m security.secrets --reencrypt"
        )
        self._try_xor_legacy(raw)

    def _load_or_create_key(self) -> bytes:
        """Return the Fernet key, generating and saving it if it doesn't exist."""
        Fernet = _get_fernet()
        if self._key_file.exists():
            return self._key_file.read_bytes().strip()
        key = Fernet.generate_key()
        self._key_file.parent.mkdir(parents=True, exist_ok=True)
        self._key_file.write_bytes(key)
        logger.warning(
            "SecretsManager: generated new Fernet key at %s — "
            "back this file up securely and keep it separate from the secrets file",
            self._key_file,
        )
        return key

    def _try_fernet(self, raw: bytes) -> bool:
        """Attempt Fernet decryption. Returns True on success."""
        if not self._key_file.exists():
            return False
        try:
            from cryptography.fernet import Fernet, InvalidToken
            key = self._key_file.read_bytes().strip()
            f   = Fernet(key)
            plaintext = f.decrypt(raw)
            self._cache = json.loads(plaintext.decode("utf-8"))
            logger.debug("SecretsManager: loaded %d secrets (Fernet)", len(self._cache))
            return True
        except Exception:
            return False

    def _try_xor_legacy(self, raw: bytes) -> None:
        """Attempt legacy XOR+base64 decryption (read-only, for migration only)."""
        try:
            decoded = base64.b64decode(raw)
            plain   = bytes(b ^ 0x42 for b in decoded)
            self._cache = json.loads(plain.decode())
        except Exception as exc:
            logger.error("SecretsManager: failed to load secrets file: %s", type(exc).__name__)

    def is_sensitive(self, key: str) -> bool:
        return key.upper() in self._SENSITIVE_KEYS

    def audit_log(self, key: str, accessor: str) -> None:
        """Log secret access audit (key name only, never value)."""
        logger.info(
            "SECRET_ACCESS key=%s accessor=%s",
            key if not self.is_sensitive(key) else "***REDACTED***",
            accessor,
        )


if __name__ == "__main__":
    import sys
    if "--reencrypt" in sys.argv:
        mgr = SecretsManager()
        mgr._load()
        if not mgr._cache:
            print("No secrets loaded — nothing to re-encrypt.")
            sys.exit(0)
        mgr.store(mgr._cache)
        print(f"Re-encrypted {len(mgr._cache)} secrets using Fernet.")
