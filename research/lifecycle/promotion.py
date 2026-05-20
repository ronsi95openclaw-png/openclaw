"""Strategy lifecycle states and promotion gate logic."""
from __future__ import annotations

from enum import Enum
from typing import Tuple

from research.types import PerformanceMetrics


class LifecycleState(Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    PAPER_TRADING = "PAPER_TRADING"
    PROBATION = "PROBATION"
    PRODUCTION = "PRODUCTION"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


# Valid (from_state, to_state) → promotion_type
# promotion_type: "auto" | "human" | "manual_only"
_VALID_TRANSITIONS: dict[tuple[LifecycleState, LifecycleState], str] = {
    (LifecycleState.EXPERIMENTAL,   LifecycleState.PAPER_TRADING):  "auto",
    (LifecycleState.PAPER_TRADING,  LifecycleState.PROBATION):      "human",
    (LifecycleState.PROBATION,      LifecycleState.PRODUCTION):     "human",
    (LifecycleState.PRODUCTION,     LifecycleState.DEGRADED):       "auto",
    (LifecycleState.DEGRADED,       LifecycleState.QUARANTINED):    "auto",
    (LifecycleState.PRODUCTION,     LifecycleState.QUARANTINED):    "auto",
    (LifecycleState.QUARANTINED,    LifecycleState.RETIRED):        "manual_only",
    (LifecycleState.DEGRADED,       LifecycleState.PRODUCTION):     "manual_only",
}

# ANY state → RETIRED is also valid (manual only)
_RETIRED_FROM_ANY = True


def is_valid_transition(
    from_state: LifecycleState,
    to_state: LifecycleState,
) -> bool:
    """Return *True* if the transition is in the allowed set."""
    if to_state == LifecycleState.RETIRED:
        return True
    return (from_state, to_state) in _VALID_TRANSITIONS


def requires_human_approval(
    from_state: LifecycleState,
    to_state: LifecycleState,
) -> bool:
    """Return *True* if this transition always requires a human operator."""
    if to_state == LifecycleState.RETIRED:
        return True
    ptype = _VALID_TRANSITIONS.get((from_state, to_state), "")
    return ptype in ("human", "manual_only")


class PromotionGate:
    """Determines whether a strategy may advance to the next lifecycle state.

    CRITICAL: Transitions to PROBATION and PRODUCTION **always** require
    human approval regardless of whether metrics pass the thresholds.
    """

    def __init__(
        self,
        min_paper_trades: int = 100,
        min_sharpe: float = 0.5,
        min_win_rate: float = 0.45,
        max_drawdown_pct: float = 25.0,
        min_probation_days: int = 30,
    ) -> None:
        self.min_paper_trades = min_paper_trades
        self.min_sharpe = min_sharpe
        self.min_win_rate = min_win_rate
        self.max_drawdown_pct = max_drawdown_pct
        self.min_probation_days = min_probation_days

    def can_advance(
        self,
        current_state: LifecycleState,
        metrics: PerformanceMetrics,
        days_in_state: int,
    ) -> Tuple[bool, str]:
        """Evaluate whether the strategy may advance from *current_state*.

        Returns
        -------
        (allowed, reason)
            *allowed* is ``True`` only when **all** metric thresholds pass
            AND any mandatory waiting period has elapsed.

        CRITICAL: For PAPER_TRADING → PROBATION and PROBATION → PRODUCTION,
        this method will always return ``(False, "requires human approval")``.
        Human approval must be obtained through the governance workflow
        before calling :meth:`StrategyLifecycleManager.transition`.
        """
        if current_state == LifecycleState.EXPERIMENTAL:
            if metrics.total_trades < self.min_paper_trades:
                return False, (
                    f"insufficient paper trades: {metrics.total_trades} "
                    f"< {self.min_paper_trades}"
                )
            return True, "auto-advance criteria met"

        if current_state == LifecycleState.PAPER_TRADING:
            # Always gate on human approval
            return False, (
                "PAPER_TRADING → PROBATION requires human approval"
            )

        if current_state == LifecycleState.PROBATION:
            # Always gate on human approval; check time and metrics for reference
            if days_in_state < self.min_probation_days:
                return False, (
                    f"minimum probation period not elapsed: "
                    f"{days_in_state}d < {self.min_probation_days}d; "
                    "also requires human approval"
                )
            return False, (
                "PROBATION → PRODUCTION requires human approval"
            )

        return False, f"no automatic advancement from state {current_state.value}"

    def metrics_pass(self, metrics: PerformanceMetrics) -> Tuple[bool, str]:
        """Check whether *metrics* satisfy promotion thresholds.

        This is a helper used by the governance workflow to show operators
        which criteria have been met.
        """
        issues: list[str] = []
        if metrics.sharpe_ratio < self.min_sharpe:
            issues.append(
                f"sharpe_ratio {metrics.sharpe_ratio:.3f} < {self.min_sharpe}"
            )
        if metrics.win_rate < self.min_win_rate:
            issues.append(
                f"win_rate {metrics.win_rate:.3f} < {self.min_win_rate}"
            )
        if metrics.max_drawdown_pct > self.max_drawdown_pct:
            issues.append(
                f"max_drawdown {metrics.max_drawdown_pct:.1f}% "
                f"> {self.max_drawdown_pct}%"
            )
        if issues:
            return False, "; ".join(issues)
        return True, "all metric thresholds satisfied"
