"""Deployment gate — final safety check before a strategy reaches production."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from research.types import PerformanceMetrics


class DeploymentBlockedError(RuntimeError):
    """Raised when a strategy attempts to deploy without proper approval."""


class GovernanceError(RuntimeError):
    """Raised for governance workflow violations."""


class DeploymentGate:
    """Final check before any strategy reaches production.

    CRITICAL: This gate MUST be called by the bot layer before activating
    any strategy.  Attempting to deploy without governance approval raises
    :class:`DeploymentBlockedError`.

    Approved strategies are tracked in an internal registry keyed by
    strategy name.  Approval is only granted through
    :meth:`approve_deployment`, which is called by the governance workflow
    after human review is complete.
    """

    def __init__(self, governance_queue: Optional[Any] = None) -> None:
        self._governance_queue = governance_queue
        # {strategy: {review_id, operator_id, metrics, approved}}
        self._registry: Dict[str, Dict[str, Any]] = {}

    def request_deployment(
        self,
        strategy: str,
        operator_id: str,
        metrics: PerformanceMetrics,
    ) -> str:
        """Submit a deployment request to the governance review queue.

        Parameters
        ----------
        strategy:
            Strategy identifier.
        operator_id:
            Identity of the operator requesting deployment.
        metrics:
            Current performance metrics supporting the request.

        Returns
        -------
        str
            A unique *review_id* for tracking the governance request.

        Raises
        ------
        DeploymentBlockedError
            If no governance queue has been configured.
        """
        if self._governance_queue is None:
            raise DeploymentBlockedError(
                "governance_queue is not set — deployment requests cannot "
                "be submitted.  Configure a governance queue before calling "
                "request_deployment()."
            )

        review_id = str(uuid.uuid4())
        record: Dict[str, Any] = {
            "review_id": review_id,
            "operator_id": operator_id,
            "metrics": metrics,
            "approved": False,
        }
        self._registry[strategy] = record

        # Delegate to the governance queue implementation
        if hasattr(self._governance_queue, "submit"):
            self._governance_queue.submit(strategy, review_id, operator_id, metrics)

        return review_id

    def approve_deployment(self, strategy: str, review_id: str) -> None:
        """Mark a pending deployment request as approved.

        This method is called by the governance workflow after human review
        is complete.  It is **not** intended to be called by automated
        processes.
        """
        record = self._registry.get(strategy)
        if record is None:
            raise GovernanceError(
                f"no pending deployment request found for strategy '{strategy}'"
            )
        if record["review_id"] != review_id:
            raise GovernanceError(
                f"review_id mismatch for strategy '{strategy}'"
            )
        record["approved"] = True

    def is_approved_for_production(self, strategy: str) -> bool:
        """Return *True* only if a governance-approved record exists.

        Note: This check is necessary but not sufficient — the
        :class:`~research.lifecycle.manager.StrategyLifecycleManager` must
        also confirm the strategy state is PRODUCTION.
        """
        record = self._registry.get(strategy)
        return record is not None and record.get("approved", False)

    def block_unapproved_strategy(self, strategy: str) -> None:
        """Raise :class:`DeploymentBlockedError` if the strategy is not approved.

        Called by the bot layer before each trade to enforce the governance
        gate.
        """
        if not self.is_approved_for_production(strategy):
            raise DeploymentBlockedError(
                f"strategy '{strategy}' is not approved for production.  "
                "Complete the governance review workflow before trading."
            )
