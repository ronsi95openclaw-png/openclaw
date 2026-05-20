"""StrategyLifecycleManager — orchestrates the full strategy lifecycle."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from research.lifecycle.deployment_gate import DeploymentBlockedError, GovernanceError
from research.lifecycle.promotion import (
    LifecycleState,
    PromotionGate,
    is_valid_transition,
    requires_human_approval,
)
from research.lifecycle.quarantine import QuarantineManager
from research.lifecycle.retirement import RetirementChecker
from research.types import PerformanceMetrics


@dataclass
class LifecycleRecord:
    """Persistent record for a single strategy."""
    name: str
    version: str
    state: LifecycleState
    registered_at: str          # ISO-8601
    last_transition_at: str     # ISO-8601
    days_in_state: int = 0
    days_degraded: int = 0
    days_neg_sharpe: int = 0
    days_low_pf: int = 0
    metrics: Optional[Dict[str, Any]] = None
    baseline_metrics: Optional[Dict[str, Any]] = None
    operator_id: str = "system"
    notes: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metrics_to_dict(m: PerformanceMetrics) -> Dict[str, Any]:
    import dataclasses
    return dataclasses.asdict(m)


class StrategyLifecycleManager:
    """Orchestrates registration, promotion, degradation, and retirement.

    Persistence
    -----------
    Each strategy is stored as ``{persist_path}/{strategy}.json``.
    Every transition is appended to ``{persist_path}/transitions.jsonl``
    (append-only, immutable audit log).
    """

    def __init__(
        self,
        persist_path: str = "data/lifecycle",
        promotion_gate: Optional[PromotionGate] = None,
        quarantine_manager: Optional[QuarantineManager] = None,
        retirement_checker: Optional[RetirementChecker] = None,
    ) -> None:
        self.persist_path = persist_path
        self._gate = promotion_gate or PromotionGate()
        self._quarantine = quarantine_manager or QuarantineManager()
        self._retirement = retirement_checker or RetirementChecker()
        self._records: Dict[str, LifecycleRecord] = {}
        os.makedirs(persist_path, exist_ok=True)
        self._load_all()

    # ── Registration ──────────────────────────────────────────────────────────

    def register_strategy(self, name: str, version: str = "1.0.0") -> None:
        """Register a new strategy, starting in EXPERIMENTAL state."""
        if name in self._records:
            return  # already registered; idempotent
        now = _now_iso()
        record = LifecycleRecord(
            name=name,
            version=version,
            state=LifecycleState.EXPERIMENTAL,
            registered_at=now,
            last_transition_at=now,
        )
        self._records[name] = record
        self._persist(name)
        self._log_transition(
            name=name,
            from_state=None,
            to_state=LifecycleState.EXPERIMENTAL,
            operator_id="system",
            reason="initial registration",
        )

    # ── State query ───────────────────────────────────────────────────────────

    def get_state(self, name: str) -> LifecycleState:
        """Return current :class:`LifecycleState` for *name*."""
        if name not in self._records:
            raise KeyError(f"strategy '{name}' is not registered")
        return self._records[name].state

    # ── Transitions ───────────────────────────────────────────────────────────

    def transition(
        self,
        name: str,
        target_state: LifecycleState,
        operator_id: str,
        reason: str,
    ) -> bool:
        """Attempt to transition *name* to *target_state*.

        Validates that:
        1. The transition is in the allowed set.
        2. Human approval is provided where required.
        3. Automated promotion to PRODUCTION is never allowed — raises
           :class:`GovernanceError` if attempted without operator approval.

        Returns
        -------
        bool
            ``True`` on success.

        Raises
        ------
        KeyError
            If the strategy is not registered.
        GovernanceError
            If an illegal automated promotion to PRODUCTION is attempted.
        ValueError
            If the transition is not valid.
        """
        if name not in self._records:
            raise KeyError(f"strategy '{name}' is not registered")

        record = self._records[name]
        from_state = record.state

        if not is_valid_transition(from_state, target_state):
            raise ValueError(
                f"invalid transition {from_state.value} → {target_state.value}"
            )

        # Guard against automated promotion to PRODUCTION
        if target_state == LifecycleState.PRODUCTION and operator_id == "system":
            raise GovernanceError(
                "automated promotion to PRODUCTION is forbidden. "
                "An operator must approve via the governance workflow."
            )

        # Human-required transitions must be initiated by a non-system actor
        if requires_human_approval(from_state, target_state) and operator_id == "system":
            raise GovernanceError(
                f"transition {from_state.value} → {target_state.value} "
                "requires human approval; operator_id cannot be 'system'."
            )

        record.state = target_state
        record.last_transition_at = _now_iso()
        record.days_in_state = 0
        record.operator_id = operator_id
        if target_state != LifecycleState.DEGRADED:
            record.days_degraded = 0

        self._persist(name)
        self._log_transition(
            name=name,
            from_state=from_state,
            to_state=target_state,
            operator_id=operator_id,
            reason=reason,
        )
        return True

    # ── Metrics update ────────────────────────────────────────────────────────

    def update_metrics(self, name: str, metrics: PerformanceMetrics) -> None:
        """Store the latest performance metrics for *name*."""
        if name not in self._records:
            raise KeyError(f"strategy '{name}' is not registered")
        record = self._records[name]
        record.metrics = _metrics_to_dict(metrics)
        # Set baseline on first update if not already set
        if record.baseline_metrics is None:
            record.baseline_metrics = _metrics_to_dict(metrics)
        self._persist(name)

    # ── Degradation sweep ─────────────────────────────────────────────────────

    def check_all_degradations(self) -> List[str]:
        """Evaluate all PRODUCTION strategies for degradation.

        Returns the list of strategy names that were automatically moved to
        DEGRADED or QUARANTINED during this sweep.
        """
        degraded: list[str] = []

        for name, record in self._records.items():
            if record.state not in (
                LifecycleState.PRODUCTION,
                LifecycleState.DEGRADED,
            ):
                continue

            if record.metrics is None or record.baseline_metrics is None:
                continue

            current = self._dict_to_metrics(record.metrics)
            baseline = self._dict_to_metrics(record.baseline_metrics)

            if record.state == LifecycleState.PRODUCTION:
                if self._quarantine.check_degradation(name, current, baseline):
                    self.transition(
                        name=name,
                        target_state=LifecycleState.DEGRADED,
                        operator_id="system",
                        reason="auto-degradation: performance thresholds breached",
                    )
                    degraded.append(name)

            elif record.state == LifecycleState.DEGRADED:
                record.days_degraded += 1
                self._persist(name)
                if self._quarantine.should_quarantine(name, record.days_degraded):
                    self.transition(
                        name=name,
                        target_state=LifecycleState.QUARANTINED,
                        operator_id="system",
                        reason=(
                            f"auto-quarantine: degraded for "
                            f"{record.days_degraded} days"
                        ),
                    )
                    degraded.append(name)

        return degraded

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_lifecycle_report(self) -> Dict[str, Any]:
        """Return a snapshot of all strategies and their lifecycle states."""
        report: Dict[str, Any] = {
            "generated_at": _now_iso(),
            "total_strategies": len(self._records),
            "by_state": {},
            "strategies": {},
        }

        state_counts: Dict[str, int] = {}
        for name, record in self._records.items():
            state_key = record.state.value
            state_counts[state_key] = state_counts.get(state_key, 0) + 1
            report["strategies"][name] = {
                "version": record.version,
                "state": record.state.value,
                "registered_at": record.registered_at,
                "last_transition_at": record.last_transition_at,
                "days_in_state": record.days_in_state,
                "operator_id": record.operator_id,
                "notes": record.notes,
            }
        report["by_state"] = state_counts
        return report

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _persist(self, name: str) -> None:
        record = self._records[name]
        path = os.path.join(self.persist_path, f"{name}.json")
        data = {
            "name": record.name,
            "version": record.version,
            "state": record.state.value,
            "registered_at": record.registered_at,
            "last_transition_at": record.last_transition_at,
            "days_in_state": record.days_in_state,
            "days_degraded": record.days_degraded,
            "days_neg_sharpe": record.days_neg_sharpe,
            "days_low_pf": record.days_low_pf,
            "metrics": record.metrics,
            "baseline_metrics": record.baseline_metrics,
            "operator_id": record.operator_id,
            "notes": record.notes,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def _load_all(self) -> None:
        for fname in os.listdir(self.persist_path):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.persist_path, fname)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                record = LifecycleRecord(
                    name=data["name"],
                    version=data.get("version", "1.0.0"),
                    state=LifecycleState(data["state"]),
                    registered_at=data["registered_at"],
                    last_transition_at=data["last_transition_at"],
                    days_in_state=data.get("days_in_state", 0),
                    days_degraded=data.get("days_degraded", 0),
                    days_neg_sharpe=data.get("days_neg_sharpe", 0),
                    days_low_pf=data.get("days_low_pf", 0),
                    metrics=data.get("metrics"),
                    baseline_metrics=data.get("baseline_metrics"),
                    operator_id=data.get("operator_id", "system"),
                    notes=data.get("notes", ""),
                )
                self._records[record.name] = record
            except (json.JSONDecodeError, KeyError):
                pass  # skip corrupted files

    def _log_transition(
        self,
        name: str,
        from_state: Optional[LifecycleState],
        to_state: LifecycleState,
        operator_id: str,
        reason: str,
    ) -> None:
        log_path = os.path.join(self.persist_path, "transitions.jsonl")
        entry = {
            "ts": _now_iso(),
            "strategy": name,
            "from_state": from_state.value if from_state else None,
            "to_state": to_state.value,
            "operator_id": operator_id,
            "reason": reason,
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    @staticmethod
    def _dict_to_metrics(d: Dict[str, Any]) -> PerformanceMetrics:
        return PerformanceMetrics(
            total_return_pct=d.get("total_return_pct", 0.0),
            annualized_return_pct=d.get("annualized_return_pct", 0.0),
            cagr=d.get("cagr", 0.0),
            sharpe_ratio=d.get("sharpe_ratio", 0.0),
            sortino_ratio=d.get("sortino_ratio", 0.0),
            calmar_ratio=d.get("calmar_ratio", 0.0),
            omega_ratio=d.get("omega_ratio", 0.0),
            max_drawdown_pct=d.get("max_drawdown_pct", 0.0),
            max_drawdown_duration_bars=d.get("max_drawdown_duration_bars", 0),
            recovery_factor=d.get("recovery_factor", 0.0),
            total_trades=d.get("total_trades", 0),
            winning_trades=d.get("winning_trades", 0),
            losing_trades=d.get("losing_trades", 0),
            win_rate=d.get("win_rate", 0.0),
            profit_factor=d.get("profit_factor", 0.0),
            payoff_ratio=d.get("payoff_ratio", 0.0),
            expectancy=d.get("expectancy", 0.0),
            avg_win=d.get("avg_win", 0.0),
            avg_loss=d.get("avg_loss", 0.0),
            largest_win=d.get("largest_win", 0.0),
            largest_loss=d.get("largest_loss", 0.0),
            max_win_streak=d.get("max_win_streak", 0),
            max_loss_streak=d.get("max_loss_streak", 0),
            avg_holding_bars=d.get("avg_holding_bars", 0.0),
            total_fees=d.get("total_fees", 0.0),
            total_slippage=d.get("total_slippage", 0.0),
        )
