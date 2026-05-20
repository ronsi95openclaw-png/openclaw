"""Operator control panel for global halt, risk overrides, and halt release.

AI SAFETY CONTRACT:
- This module NEVER calls exchange APIs directly.
- Halt flags are read by ExecutionManager, which enforces them.
- All mutations are append-logged and require validated permissions.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from governance.approvals import ApprovalQueue, ApprovalRequest, ApprovalStatus
from governance.permissions import OperatorPermission, PermissionRegistry

logger = logging.getLogger("openclaw.governance.operator_controls")

_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def _fire_kill_switch(reason: str) -> None:
    """Increment the KILL_SWITCH_EVENTS Prometheus counter (best-effort)."""
    try:
        from core.metrics import KILL_SWITCH_EVENTS
        KILL_SWITCH_EVENTS.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


class OperatorControls:
    """Provides human operator levers for global halt and risk overrides.

    Thread-safe; all flag mutations hold self._lock.
    State is written to an append-only JSONL log.
    """

    def __init__(
        self,
        permission_registry: Optional[PermissionRegistry] = None,
        approval_queue: Optional[ApprovalQueue] = None,
        log_dir: Optional[str] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._log_dir = log_dir or os.environ.get("GOVERNANCE_LOG_DIR", _DEFAULT_LOG_DIR)
        os.makedirs(self._log_dir, exist_ok=True)
        self._log_path = os.path.join(self._log_dir, "operator_controls.jsonl")

        self._permissions = permission_registry or PermissionRegistry(log_dir=self._log_dir)
        self._approvals   = approval_queue or ApprovalQueue(log_dir=self._log_dir)

        # Halt state.
        self._global_halt: bool = False
        self._halt_reason: str = ""
        self._halt_operator: str = ""
        self._halt_timestamp: Optional[datetime] = None

        # Risk override state.
        self._risk_override_active: bool = False
        self._risk_override_scalar: float = 1.0
        self._risk_override_expires: Optional[datetime] = None
        self._risk_override_operator: str = ""

    # ── Halt controls ─────────────────────────────────────────────────────

    def global_halt(self, operator_id: str, reason: str) -> bool:
        """Set the global trading halt flag.

        Requires OPERATOR or higher permission.
        Fires the KILL_SWITCH_EVENTS Prometheus counter.

        Returns:
            True  → halt set successfully.
            False → insufficient permissions.
        """
        perm = self._permissions.get_permission(operator_id)
        if perm == OperatorPermission.READ_ONLY:
            logger.warning("global_halt denied: operator=%s perm=%s",
                           operator_id, perm.value)
            return False

        now = datetime.now(timezone.utc)
        with self._lock:
            self._global_halt = True
            self._halt_reason = reason
            self._halt_operator = operator_id
            self._halt_timestamp = now
            self._append_log({
                "event":       "global_halt",
                "operator_id": operator_id,
                "reason":      reason,
                "ts":          now.isoformat(),
            })

        _fire_kill_switch(reason=f"operator_halt:{reason[:80]}")
        logger.warning("GLOBAL HALT SET by %s: %s", operator_id, reason)
        return True

    def release_halt(self, operator_id: str, reason: str) -> bool:
        """Release the global halt.

        Requires ADMIN permission.  If the caller is not ADMIN, an ApprovalRequest
        is created and False is returned (caller must obtain approval).

        Returns:
            True  → halt released immediately (caller is ADMIN).
            False → insufficient permissions or ApprovalRequest created.
        """
        perm = self._permissions.get_permission(operator_id)
        if perm == OperatorPermission.ADMIN:
            now = datetime.now(timezone.utc)
            with self._lock:
                self._global_halt = False
                self._append_log({
                    "event":       "release_halt",
                    "operator_id": operator_id,
                    "reason":      reason,
                    "ts":          now.isoformat(),
                })
            logger.warning("GLOBAL HALT RELEASED by ADMIN %s: %s", operator_id, reason)
            return True

        # Non-ADMIN: submit an approval request.
        request = ApprovalRequest(
            request_type="risk_override",
            requester=operator_id,
            description=f"Request to release global halt. Reason: {reason}",
            payload={"action": "release_halt", "reason": reason},
        )
        self._approvals.submit(request)
        logger.info("release_halt: non-ADMIN %s submitted approval request %s",
                    operator_id, request.request_id)
        return False

    # ── Risk override ─────────────────────────────────────────────────────

    def set_risk_override(
        self,
        operator_id: str,
        scalar: float,
        duration_hours: int,
    ) -> bool:
        """Manually override the risk scalar for a limited duration.

        Requires OPERATOR or higher permission.
        scalar must be in [0.0, 2.0]; duration_hours must be positive.

        Returns:
            True  → override set.
            False → permission denied or invalid parameters.
        """
        perm = self._permissions.get_permission(operator_id)
        if perm == OperatorPermission.READ_ONLY:
            logger.warning("set_risk_override denied: operator=%s", operator_id)
            return False

        if not (0.0 <= scalar <= 2.0):
            logger.warning("set_risk_override: invalid scalar=%.3f", scalar)
            return False
        if duration_hours <= 0:
            logger.warning("set_risk_override: invalid duration=%d", duration_hours)
            return False

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=duration_hours)

        with self._lock:
            self._risk_override_active   = True
            self._risk_override_scalar   = scalar
            self._risk_override_expires  = expires
            self._risk_override_operator = operator_id
            self._append_log({
                "event":          "set_risk_override",
                "operator_id":    operator_id,
                "scalar":         scalar,
                "duration_hours": duration_hours,
                "expires_at":     expires.isoformat(),
                "ts":             now.isoformat(),
            })

        logger.info("Risk override set by %s: scalar=%.3f expires=%s",
                    operator_id, scalar, expires.isoformat())
        return True

    # ── State accessor ────────────────────────────────────────────────────

    def get_control_state(self) -> Dict[str, Any]:
        """Return current control state for dashboard / ExecutionManager."""
        now = datetime.now(timezone.utc)
        with self._lock:
            # Auto-expire risk override.
            if (self._risk_override_active and
                    self._risk_override_expires is not None and
                    now > self._risk_override_expires):
                self._risk_override_active = False
                self._append_log({
                    "event": "risk_override_expired",
                    "ts":    now.isoformat(),
                })

            return {
                "global_halt":             self._global_halt,
                "halt_reason":             self._halt_reason,
                "halt_operator":           self._halt_operator,
                "halt_timestamp":          (
                    self._halt_timestamp.isoformat()
                    if self._halt_timestamp else None
                ),
                "risk_override_active":    self._risk_override_active,
                "risk_override_scalar":    (
                    self._risk_override_scalar
                    if self._risk_override_active else 1.0
                ),
                "risk_override_expires":   (
                    self._risk_override_expires.isoformat()
                    if self._risk_override_expires else None
                ),
                "risk_override_operator":  self._risk_override_operator,
            }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _append_log(self, record: Dict[str, Any]) -> None:
        record.setdefault("_logged_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record, default=str)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
