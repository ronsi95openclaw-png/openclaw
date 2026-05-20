"""Strategy lifecycle review queue.

Strategies MAY NEVER auto-promote to production.  This module enforces that
invariant: promote() raises GovernanceError if no approval exists.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from governance.approvals import ApprovalQueue, ApprovalRequest, ApprovalStatus
from governance.permissions import OperatorPermission, PermissionRegistry

logger = logging.getLogger("openclaw.governance.review_queue")

_DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


class GovernanceError(Exception):
    """Raised when a governance constraint is violated."""


class StrategyReviewQueue:
    """Human review gate for strategy lifecycle transitions.

    Every transition to production MUST have an APPROVED ApprovalRequest.
    Automated promotion is explicitly forbidden; this class enforces that.
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
        self._log_path = os.path.join(self._log_dir, "review_queue.jsonl")

        self._permissions = permission_registry or PermissionRegistry(log_dir=self._log_dir)
        self._approvals   = approval_queue or ApprovalQueue(log_dir=self._log_dir)

        # review_id → review dict
        self._reviews: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def submit_for_review(
        self,
        strategy_name: str,
        lifecycle_stage: str,
        metrics_dict: Dict[str, Any],
        requester: str,
    ) -> str:
        """Submit a strategy lifecycle transition for human review.

        Args:
            strategy_name:   Unique strategy identifier.
            lifecycle_stage: Target stage, e.g. "paper_trading", "production".
            metrics_dict:    Performance metrics to include in the review request.
            requester:       operator_id of the submitter.

        Returns:
            review_id (UUID string).
        """
        review_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Create an ApprovalRequest for strategy_promote.
        approval = ApprovalRequest(
            request_type="strategy_promote",
            requester=requester,
            description=(
                f"Promote {strategy_name!r} to lifecycle stage "
                f"{lifecycle_stage!r}"
            ),
            payload={
                "review_id":       review_id,
                "strategy_name":   strategy_name,
                "lifecycle_stage": lifecycle_stage,
                "metrics":         metrics_dict,
            },
        )
        approval_id = self._approvals.submit(approval)

        review: Dict[str, Any] = {
            "review_id":       review_id,
            "strategy_name":   strategy_name,
            "lifecycle_stage": lifecycle_stage,
            "metrics":         metrics_dict,
            "requester":       requester,
            "approval_id":     approval_id,
            "status":          "PENDING_REVIEW",
            "created_at":      now.isoformat(),
            "resolved_at":     None,
            "resolver":        None,
            "resolution_note": "",
        }

        with self._lock:
            self._reviews[review_id] = review
            self._append_log({"event": "submit", **review})

        logger.info("StrategyReview submitted: review_id=%s strategy=%s stage=%s",
                    review_id, strategy_name, lifecycle_stage)
        return review_id

    def promote(self, review_id: str) -> None:
        """Programmatic hook to finalise a promotion.

        IMPORTANT: This method RAISES GovernanceError if the underlying
        ApprovalRequest has not been explicitly approved by a human operator.
        Automated systems MUST NOT call this before approval.

        Args:
            review_id: The review_id returned by submit_for_review().

        Raises:
            GovernanceError: Always, if the approval is not in APPROVED state.
        """
        with self._lock:
            review = self._reviews.get(review_id)

        if review is None:
            raise GovernanceError(f"review_id {review_id!r} not found")

        approval_id = review["approval_id"]
        approval = self._approvals.get(approval_id)

        if approval is None or approval.status != ApprovalStatus.APPROVED:
            status_val = approval.status.value if approval else "NOT_FOUND"
            raise GovernanceError(
                f"Cannot promote strategy {review['strategy_name']!r} "
                f"(review_id={review_id}): approval status is {status_val}. "
                "Strategies may NEVER auto-promote to production. "
                "Obtain explicit ADMIN approval via the governance pipeline."
            )

        # Promotion is authorised.
        now = datetime.now(timezone.utc)
        with self._lock:
            review["status"]          = "PROMOTED"
            review["resolved_at"]     = now.isoformat()
            review["resolver"]        = approval.resolver
            review["resolution_note"] = approval.resolution_note
            self._append_log({"event": "promote", **review})

        logger.info("Strategy PROMOTED: review_id=%s strategy=%s stage=%s",
                    review_id, review["strategy_name"], review["lifecycle_stage"])

    def approve_transition(
        self, review_id: str, operator_id: str, note: str = ""
    ) -> bool:
        """Approve a pending review transition.

        Returns True on success.
        """
        review = self._get_review(review_id)
        if review is None:
            return False

        approved = self._approvals.approve(review["approval_id"], operator_id, note)
        if approved:
            now = datetime.now(timezone.utc)
            with self._lock:
                review["status"]          = "APPROVED"
                review["resolved_at"]     = now.isoformat()
                review["resolver"]        = operator_id
                review["resolution_note"] = note
                self._append_log({"event": "approve_transition", **review})

        return approved

    def reject_transition(
        self, review_id: str, operator_id: str, reason: str
    ) -> bool:
        """Reject a pending review transition.

        Returns True on success.
        """
        review = self._get_review(review_id)
        if review is None:
            return False

        rejected = self._approvals.reject(review["approval_id"], operator_id, reason)
        if rejected:
            now = datetime.now(timezone.utc)
            with self._lock:
                review["status"]          = "REJECTED"
                review["resolved_at"]     = now.isoformat()
                review["resolver"]        = operator_id
                review["resolution_note"] = reason
                self._append_log({"event": "reject_transition", **review})

        return rejected

    def get_status(self, review_id: str) -> Dict[str, Any]:
        """Return current review status dict (copy)."""
        with self._lock:
            review = self._reviews.get(review_id)
        if review is None:
            return {"error": f"review_id {review_id!r} not found"}
        return dict(review)

    def list_pending(self) -> List[Dict[str, Any]]:
        """Return all reviews with status PENDING_REVIEW."""
        with self._lock:
            return [
                dict(r) for r in self._reviews.values()
                if r["status"] == "PENDING_REVIEW"
            ]

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._reviews.get(review_id)

    def _append_log(self, record: Dict[str, Any]) -> None:
        record.setdefault("_logged_at", datetime.now(timezone.utc).isoformat())
        line = json.dumps(record, default=str)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _load(self) -> None:
        if not os.path.exists(self._log_path):
            return
        seen: Dict[str, Dict[str, Any]] = {}
        with open(self._log_path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                    record.pop("event", None)
                    record.pop("_logged_at", None)
                    rid = record.get("review_id")
                    if rid:
                        seen[rid] = record
                except Exception as exc:  # noqa: BLE001
                    logger.warning("review_queue log line %d parse error: %s", lineno, exc)
        self._reviews = seen
        logger.debug("StrategyReviewQueue loaded %d review(s) from log", len(seen))
