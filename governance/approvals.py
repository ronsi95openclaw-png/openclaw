"""Approval queue for governance-controlled actions.

All requests are persisted as an append-only JSONL file.
Thread-safe; never deletes or modifies existing log entries.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.governance.approvals")

# Default log directory, can be overridden via GOVERNANCE_LOG_DIR env var.
_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


# ── Enums ─────────────────────────────────────────────────────────────────────

class ApprovalStatus(Enum):
    PENDING  = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED  = "EXPIRED"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    request_type:    str   # "strategy_promote" | "strategy_deploy" |
                           # "risk_override" | "emergency_halt_reset" |
                           # "parameter_change"
    requester:       str
    description:     str
    payload:         Dict[str, Any]
    request_id:      str                  = field(default_factory=lambda: str(uuid.uuid4()))
    status:          ApprovalStatus       = ApprovalStatus.PENDING
    created_at:      datetime             = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at:     Optional[datetime]   = None
    resolver:        Optional[str]        = None
    resolution_note: str                  = ""
    expires_at:      Optional[datetime]   = None   # None → 24h from created_at

    def __post_init__(self) -> None:
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(hours=24)

    def is_expired(self, as_of: Optional[datetime] = None) -> bool:
        if as_of is None:
            as_of = datetime.now(timezone.utc)
        return (self.status == ApprovalStatus.PENDING and
                self.expires_at is not None and
                as_of > self.expires_at)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["created_at"] = self.created_at.isoformat()
        d["resolved_at"] = self.resolved_at.isoformat() if self.resolved_at else None
        d["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApprovalRequest":
        d = dict(d)
        d["status"] = ApprovalStatus(d["status"])
        d["created_at"] = datetime.fromisoformat(d["created_at"])
        if d.get("resolved_at"):
            d["resolved_at"] = datetime.fromisoformat(d["resolved_at"])
        if d.get("expires_at"):
            d["expires_at"] = datetime.fromisoformat(d["expires_at"])
        return cls(**d)


# ── Queue ─────────────────────────────────────────────────────────────────────

class ApprovalQueue:
    """Thread-safe, append-only JSONL-backed approval queue.

    Invariants:
    - Log entries are never deleted or modified.
    - A PENDING request may be approved, rejected, or expired.
    - Automated systems may NOT approve their own requests.
    """

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._log_dir = log_dir or os.environ.get("GOVERNANCE_LOG_DIR", _DEFAULT_LOG_DIR)
        os.makedirs(self._log_dir, exist_ok=True)
        self._log_path = os.path.join(self._log_dir, "approvals.jsonl")

        # In-memory index: request_id → ApprovalRequest
        self._requests: Dict[str, ApprovalRequest] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def submit(self, request: ApprovalRequest) -> str:
        """Enqueue a new approval request; return its request_id."""
        with self._lock:
            if request.request_id in self._requests:
                raise ValueError(
                    f"request_id {request.request_id!r} already exists"
                )
            self._requests[request.request_id] = request
            self._append_log({"event": "submit", **request.to_dict()})
            logger.info("ApprovalRequest submitted: id=%s type=%s requester=%s",
                        request.request_id, request.request_type, request.requester)
        return request.request_id

    def approve(self, request_id: str, operator_id: str, note: str = "") -> bool:
        """Approve a pending request.

        Returns True on success, False if the request was not found or not PENDING.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                logger.warning("approve: request_id=%s not found", request_id)
                return False
            if req.status != ApprovalStatus.PENDING:
                logger.warning("approve: request_id=%s status=%s (not PENDING)",
                               request_id, req.status.value)
                return False
            if req.is_expired():
                req.status = ApprovalStatus.EXPIRED
                self._append_log({"event": "auto_expire", **req.to_dict()})
                logger.info("approve: request_id=%s expired before approval", request_id)
                return False

            req.status = ApprovalStatus.APPROVED
            req.resolver = operator_id
            req.resolved_at = datetime.now(timezone.utc)
            req.resolution_note = note
            self._append_log({"event": "approve", **req.to_dict()})
            logger.info("ApprovalRequest approved: id=%s by=%s", request_id, operator_id)
        return True

    def reject(self, request_id: str, operator_id: str, note: str = "") -> bool:
        """Reject a pending request.

        Returns True on success, False if the request was not found or not PENDING.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                logger.warning("reject: request_id=%s not found", request_id)
                return False
            if req.status != ApprovalStatus.PENDING:
                logger.warning("reject: request_id=%s status=%s (not PENDING)",
                               request_id, req.status.value)
                return False

            req.status = ApprovalStatus.REJECTED
            req.resolver = operator_id
            req.resolved_at = datetime.now(timezone.utc)
            req.resolution_note = note
            self._append_log({"event": "reject", **req.to_dict()})
            logger.info("ApprovalRequest rejected: id=%s by=%s", request_id, operator_id)
        return True

    def get(self, request_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._requests.get(request_id)

    def list_pending(self) -> List[ApprovalRequest]:
        with self._lock:
            return [r for r in self._requests.values()
                    if r.status == ApprovalStatus.PENDING]

    def list_all(self, limit: int = 100) -> List[ApprovalRequest]:
        with self._lock:
            items = sorted(self._requests.values(),
                           key=lambda r: r.created_at, reverse=True)
            return items[:limit]

    def expire_old(self) -> int:
        """Expire all PENDING requests past their expiry time; return count expired."""
        now = datetime.now(timezone.utc)
        expired_count = 0
        with self._lock:
            for req in self._requests.values():
                if req.is_expired(as_of=now):
                    req.status = ApprovalStatus.EXPIRED
                    req.resolved_at = now
                    self._append_log({"event": "auto_expire", **req.to_dict()})
                    expired_count += 1
        if expired_count:
            logger.info("expire_old: expired %d request(s)", expired_count)
        return expired_count

    # ── Internal helpers ──────────────────────────────────────────────────

    def _append_log(self, record: Dict[str, Any]) -> None:
        """Append a JSON line to the immutable log file."""
        record.setdefault("_logged_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record, default=str)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _load(self) -> None:
        """Replay the log file to reconstruct in-memory state on startup."""
        if not os.path.exists(self._log_path):
            return

        seen: Dict[str, ApprovalRequest] = {}
        with open(self._log_path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                    # Strip internal log metadata before reconstructing dataclass.
                    record.pop("event", None)
                    record.pop("_logged_at", None)
                    req = ApprovalRequest.from_dict(record)
                    seen[req.request_id] = req
                except Exception as exc:  # noqa: BLE001
                    logger.warning("approvals log line %d parse error: %s", lineno, exc)

        self._requests = seen
        logger.debug("ApprovalQueue loaded %d request(s) from log", len(seen))
