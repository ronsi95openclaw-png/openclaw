"""Emergency halt controls.

AI SAFETY CONTRACT:
- Sets an immutable halt flag that cannot be reversed without ADMIN approval.
- NEVER calls exchange APIs directly.
- Halt flag is read by ExecutionManager, which enforces it.
- Maker/checker: the operator who set the halt may NOT release it.
- Logs to both governance/logs/emergency.jsonl and stdout.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from governance.approvals import ApprovalQueue, ApprovalRequest, ApprovalStatus
from governance.permissions import OperatorPermission, PermissionRegistry

logger = logging.getLogger("openclaw.governance.emergency_controls")

_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def _fire_kill_switch(reason: str) -> None:
    try:
        from core.metrics import KILL_SWITCH_EVENTS
        KILL_SWITCH_EVENTS.labels(reason=reason).inc()
    except Exception:  # noqa: BLE001
        pass


class EmergencyControls:
    """Immutable emergency halt gate.

    The halt flag can only be cleared by:
        1. An ADMIN operator (not the same one who set the halt),
        2. After an explicit ApprovalRequest is created and approved.

    All halt events are logged to ``governance/logs/emergency.jsonl``
    and printed to stdout so that they survive log rotation and appear in
    container stdout even if structured logging is misconfigured.
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
        self._log_path = os.path.join(self._log_dir, "emergency.jsonl")

        self._permissions = permission_registry or PermissionRegistry(log_dir=self._log_dir)
        self._approvals   = approval_queue or ApprovalQueue(log_dir=self._log_dir)

        # Halt state.
        self._halted: bool = False
        self._halt_operator: str = ""
        self._halt_reason: str = ""
        self._halt_timestamp: Optional[datetime] = None

        # Pending release approval request ids.
        self._pending_release_ids: List[str] = []

    # ── Public API ────────────────────────────────────────────────────────

    def emergency_halt_all(self, reason: str, operator_id: str) -> None:
        """Immediately halt all trading.

        Sets an immutable halt flag.  Logs to emergency.jsonl and stdout.
        Fires KILL_SWITCH_EVENTS Prometheus counter.

        The flag CANNOT be reversed without ADMIN approval obtained via
        request_halt_release().
        """
        now = datetime.now(timezone.utc)
        event: Dict[str, Any] = {
            "event":       "EMERGENCY_HALT",
            "operator_id": operator_id,
            "reason":      reason,
            "ts":          now.isoformat(),
        }

        with self._lock:
            self._halted         = True
            self._halt_operator  = operator_id
            self._halt_reason    = reason
            self._halt_timestamp = now
            self._append_log(event)

        _fire_kill_switch(reason=f"emergency_halt:{reason[:80]}")

        # Mandatory stdout echo (belt-and-braces).
        msg = (
            f"[EMERGENCY_HALT] {now.isoformat()} "
            f"operator={operator_id!r} reason={reason!r}"
        )
        print(msg, file=sys.stdout, flush=True)
        logger.critical(msg)

    def request_halt_release(self, operator_id: str, reason: str) -> str:
        """Create an ApprovalRequest to release the emergency halt.

        Maker/checker enforced: the operator who triggered the halt may NOT
        be the same one who requests the release.

        Returns:
            approval_request_id (UUID string).

        Raises:
            PermissionError: If the caller is the operator who triggered the halt.
            RuntimeError:    If no emergency halt is currently active.
        """
        with self._lock:
            if not self._halted:
                raise RuntimeError("No emergency halt is currently active.")
            if operator_id == self._halt_operator:
                raise PermissionError(
                    f"Maker/checker violation: operator {operator_id!r} triggered "
                    "the emergency halt and may NOT request its release. "
                    "A DIFFERENT ADMIN must initiate the release."
                )

        request = ApprovalRequest(
            request_type="emergency_halt_reset",
            requester=operator_id,
            description=(
                f"Request to release emergency halt "
                f"(originally set by {self._halt_operator!r}). "
                f"Release reason: {reason}"
            ),
            payload={
                "action":            "release_emergency_halt",
                "release_reason":    reason,
                "halt_operator":     self._halt_operator,
                "halt_reason":       self._halt_reason,
                "halt_timestamp":    (
                    self._halt_timestamp.isoformat()
                    if self._halt_timestamp else None
                ),
            },
        )
        request_id = self._approvals.submit(request)

        with self._lock:
            self._pending_release_ids.append(request_id)
            self._append_log({
                "event":      "request_halt_release",
                "request_id": request_id,
                "operator_id": operator_id,
                "reason":     reason,
                "ts":         datetime.now(timezone.utc).isoformat(),
            })

        logger.info("Emergency halt release requested: request_id=%s by=%s",
                    request_id, operator_id)
        return request_id

    def execute_approved_release(self, request_id: str, executing_operator: str) -> bool:
        """Execute an approved release request.

        The approval must already exist in APPROVED state.
        The executing operator must be ADMIN and must NOT be the halt-setter.

        Maker/checker uses the halt_operator captured in the approval request
        payload (not live state) to prevent a concurrent halt from bypassing
        the check-then-release gap.

        Returns:
            True  → halt released.
            False → request not approved or permission denied.
        """
        perm = self._permissions.get_permission(executing_operator)
        if perm != OperatorPermission.ADMIN:
            logger.warning("execute_approved_release: %s is not ADMIN", executing_operator)
            return False

        # Fetch approval outside the lock — ApprovalQueue has its own lock
        # and never calls back into EmergencyControls, so no deadlock risk.
        approval = self._approvals.get(request_id)
        if approval is None or approval.status != ApprovalStatus.APPROVED:
            status_val = approval.status.value if approval else "NOT_FOUND"
            logger.warning(
                "execute_approved_release: request_id=%s status=%s (need APPROVED)",
                request_id, status_val
            )
            return False

        # Use the halt_operator captured at request_halt_release() time, not
        # the live _halt_operator — a concurrent emergency_halt_all() between
        # the two lock scopes could overwrite _halt_operator, allowing the
        # wrong operator to pass the maker/checker check.
        captured_halt_operator = approval.payload.get("halt_operator", "")
        if executing_operator == captured_halt_operator:
            logger.error(
                "execute_approved_release: maker/checker violation — "
                "%s triggered the halt and cannot release it "
                "(halt_operator from request payload=%r)",
                executing_operator, captured_halt_operator,
            )
            return False

        now = datetime.now(timezone.utc)
        with self._lock:
            if not self._halted:
                logger.warning(
                    "execute_approved_release: halt already released (request_id=%s)",
                    request_id,
                )
                return False
            self._halted = False
            self._append_log({
                "event":            "EMERGENCY_HALT_RELEASED",
                "request_id":       request_id,
                "executing_op":     executing_operator,
                "original_halter":  captured_halt_operator,
                "ts":               now.isoformat(),
            })

        msg = (
            f"[EMERGENCY_HALT_RELEASED] {now.isoformat()} "
            f"by={executing_operator!r} request_id={request_id!r}"
        )
        print(msg, file=sys.stdout, flush=True)
        logger.critical(msg)
        return True

    def is_emergency_halted(self) -> bool:
        with self._lock:
            return self._halted

    def get_halt_log(self) -> List[Dict[str, Any]]:
        """Return an immutable copy of all halt-related log entries."""
        entries: List[Dict[str, Any]] = []
        if not os.path.exists(self._log_path):
            return entries

        with open(self._log_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        return entries

    # ── Internal helpers ──────────────────────────────────────────────────

    def _append_log(self, record: Dict[str, Any]) -> None:
        """Append to immutable emergency log (file-locked for multi-process safety)."""
        record.setdefault("_logged_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record, default=str)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line + "\n")
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
