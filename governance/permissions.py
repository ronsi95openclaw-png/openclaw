"""Operator permission registry.

Permissions are persisted to a JSON file.  In production, the file would be
encrypted at rest; here we stub encryption with base64 (clearly labelled).

Thread-safe; all mutations require an ADMIN caller.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from enum import Enum
from typing import Dict, Optional, Set

logger = logging.getLogger("openclaw.governance.permissions")

_DEFAULT_REGISTRY_DIR = os.path.join(os.path.dirname(__file__), "logs")


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
        Stored as a base64-encoded JSON blob in ``{log_dir}/permissions.b64``.
        This is a stub; production deployments should use envelope encryption.

    Thread safety:
        All public methods acquire self._lock.
    """

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._log_dir = log_dir or os.environ.get("GOVERNANCE_LOG_DIR", _DEFAULT_REGISTRY_DIR)
        os.makedirs(self._log_dir, exist_ok=True)
        self._path = os.path.join(self._log_dir, "permissions.b64")

        # operator_id → OperatorPermission
        self._registry: Dict[str, OperatorPermission] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def register(self, operator_id: str, permission_level: OperatorPermission,
                 caller_id: str) -> None:
        """Register or update an operator's permission level.

        Only an ADMIN or SYSTEM caller may modify registrations.

        Args:
            operator_id:       The operator being registered.
            permission_level:  The OperatorPermission to assign.
            caller_id:         The operator initiating the registration.
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
        """Return True if the operator may approve the given request type.

        Permission rules:
            READ_ONLY  → no approvals
            OPERATOR   → risk_override, parameter_change
            ADMIN      → all, including strategy_promote, emergency_halt_reset
            SYSTEM     → same as ADMIN (for automated test workflows)
        """
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
        """Encode registry as JSON then base64; write to disk."""
        raw = json.dumps(
            {k: v.value for k, v in self._registry.items()},
            indent=2,
        ).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        with open(self._path, "w", encoding="ascii") as fh:
            fh.write(encoded)

    def _load(self) -> None:
        """Decode and load registry from disk (if it exists)."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="ascii") as fh:
                encoded = fh.read().strip()
            raw = base64.b64decode(encoded).decode("utf-8")
            data: Dict[str, str] = json.loads(raw)
            self._registry = {k: OperatorPermission(v) for k, v in data.items()}
            logger.debug("PermissionRegistry loaded %d operator(s)", len(self._registry))
        except Exception as exc:  # noqa: BLE001
            logger.error("PermissionRegistry load failed: %s — starting empty", exc)
            self._registry = {}
