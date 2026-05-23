"""Operator permission registry.

Permissions are persisted to a Fernet-encrypted JSON file.

Thread-safe; all mutations require an ADMIN caller.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from enum import Enum
from typing import Dict, Optional, Set

logger = logging.getLogger("openclaw.governance.permissions")

_DEFAULT_REGISTRY_DIR = os.path.join(os.path.dirname(__file__), "logs")
_KEY_FILE = Path("data/permissions.key")


def _get_fernet():
    from cryptography.fernet import Fernet
    return Fernet


def _load_or_create_key() -> bytes:
    Fernet = _get_fernet()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(key)
    os.chmod(_KEY_FILE, 0o600)
    return key


# ── Permission levels ─────────────────────────────────────────────────────────

class OperatorPermission(Enum):
    READ_ONLY = "READ_ONLY"   # Dashboard / monitoring only
    OPERATOR  = "OPERATOR"    # Can approve risk & parameter changes
    ADMIN     = "ADMIN"       # Full control including emergency actions
    SYSTEM    = "SYSTEM"      # Programmatic / automated approvals (tests)


# Allowed request types per permission level (cumulative).
_OPERATOR_ALLOWED_TYPES: Set[str] = {
    "risk_override",
    "parameter_change",
}

_ADMIN_ALLOWED_TYPES: Set[str] = _OPERATOR_ALLOWED_TYPES | {
    "strategy_promote",
    "strategy_deploy",
    "emergency_halt_reset",
}

_SYSTEM_ALLOWED_TYPES: Set[str] = _ADMIN_ALLOWED_TYPES  # automated tests only


# ── Registry ──────────────────────────────────────────────────────────────────

class PermissionRegistry:
    """Registry mapping operator_id → OperatorPermission.

    Persistence:
        Stored as a Fernet-encrypted JSON blob in ``{log_dir}/permissions.fernet``.
        Key is stored in ``data/permissions.key`` (chmod 600).

    Thread safety:
        All public methods acquire self._lock.
    """

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._log_dir = log_dir or os.environ.get("GOVERNANCE_LOG_DIR", _DEFAULT_REGISTRY_DIR)
        os.makedirs(self._log_dir, exist_ok=True)
        self._path = os.path.join(self._log_dir, "permissions.fernet")

        # operator_id → OperatorPermission
        self._registry: Dict[str, OperatorPermission] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def register(self, operator_id: str, permission_level: OperatorPermission,
                 caller_id: str) -> None:
        """Register or update an operator's permission level.

        Only an ADMIN or SYSTEM caller may modify registrations.
        """
        caller_perm = self.get_permission(caller_id)
        if caller_perm not in (OperatorPermission.ADMIN, OperatorPermission.SYSTEM):
            raise PermissionError(
                f"register(): caller {caller_id!r} has permission "
                f"{caller_perm.value}, but ADMIN is required."
            )

        with self._lock:
            self._registry[operator_id] = permission_level
            self._save()
            logger.info("PermissionRegistry: registered %s as %s (by %s)",
                        operator_id, permission_level.value, caller_id)

    def get_permission(self, operator_id: str) -> OperatorPermission:
        """Return the permission level for an operator; READ_ONLY if not found."""
        with self._lock:
            return self._registry.get(operator_id, OperatorPermission.READ_ONLY)

    def can_approve(self, operator_id: str, request_type: str) -> bool:
        """Return True if the operator may approve the given request type."""
        perm = self.get_permission(operator_id)

        if perm == OperatorPermission.READ_ONLY:
            return False
        if perm == OperatorPermission.OPERATOR:
            return request_type in _OPERATOR_ALLOWED_TYPES
        if perm == OperatorPermission.ADMIN:
            return request_type in _ADMIN_ALLOWED_TYPES
        if perm == OperatorPermission.SYSTEM:
            return request_type in _SYSTEM_ALLOWED_TYPES

        return False

    # ── Persistence ───────────────────────────────────────────────────────

    def _save(self) -> None:
        """Fernet-encrypt registry JSON and write to disk."""
        try:
            Fernet = _get_fernet()
            key = _load_or_create_key()
            f = Fernet(key)
            raw = json.dumps(
                {k: v.value for k, v in self._registry.items()},
                indent=2,
            ).encode("utf-8")
            token = f.encrypt(raw)
            with open(self._path, "wb") as fh:
                fh.write(token)
        except Exception as exc:  # noqa: BLE001
            logger.error("PermissionRegistry save failed: %s", exc)

    def _load(self) -> None:
        """Decrypt and load registry from disk (if it exists)."""
        # Migrate legacy base64 file if it exists
        legacy = os.path.join(self._log_dir, "permissions.b64")
        if os.path.exists(legacy) and not os.path.exists(self._path):
            self._migrate_legacy(legacy)
            return

        if not os.path.exists(self._path):
            return
        try:
            Fernet = _get_fernet()
            key = _load_or_create_key()
            f = Fernet(key)
            with open(self._path, "rb") as fh:
                token = fh.read()
            raw = f.decrypt(token).decode("utf-8")
            data: Dict[str, str] = json.loads(raw)
            self._registry = {k: OperatorPermission(v) for k, v in data.items()}
            logger.debug("PermissionRegistry loaded %d operator(s)", len(self._registry))
        except Exception as exc:  # noqa: BLE001
            logger.error("PermissionRegistry load failed: %s — starting empty", exc)
            self._registry = {}

    def _migrate_legacy(self, legacy_path: str) -> None:
        """Migrate base64-encoded legacy file to Fernet-encrypted format."""
        import base64
        try:
            with open(legacy_path, "r", encoding="ascii") as fh:
                encoded = fh.read().strip()
            raw = base64.b64decode(encoded).decode("utf-8")
            data: Dict[str, str] = json.loads(raw)
            self._registry = {k: OperatorPermission(v) for k, v in data.items()}
            self._save()
            os.rename(legacy_path, legacy_path + ".migrated")
            logger.info("PermissionRegistry: migrated %d operators from legacy b64 to Fernet",
                        len(self._registry))
        except Exception as exc:  # noqa: BLE001
            logger.error("PermissionRegistry legacy migration failed: %s — starting empty", exc)
            self._registry = {}
