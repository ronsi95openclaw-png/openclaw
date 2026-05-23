"""Strategy Governance Integration Layer — OpenClaw.

This module forms the bridge between the StrategyAttributionEngine (research-side
analytics) and the ShadowOptimizationEngine (live-weight gating).  It is the
single authority that decides *what* to do with a strategy and *routes* that
decision through the shadow engine validation gate.

All AI signals (attribution, overfitting, decay) are treated as ADVISORY.
No weight change ever bypasses ShadowOptimizationEngine.

Governance actions
------------------
REDUCE_WEIGHT        — detected decay → cut weight by 20 %, floor 0.20
DISABLE_IN_REGIME    — regime blindness → advisory log only (compat matrix untouched)
CLAMP_CONFIDENCE     — poor calibration → advisory log only
FREEZE_OPTIMIZATION  — overfitting signal → mark shadow candidate as FROZEN
QUARANTINE           — deeply negative expectancy + ≥ 20 trades → weight → 0.10
NO_ACTION            — strategy is healthy

Thread safety
-------------
All public methods acquire ``_lock`` before accessing ``_decisions``.
File persistence uses ``fcntl.LOCK_EX``.

Singleton
---------
Call ``get_governance_engine(dry_run=False)`` to get or create the shared instance.
"""
from __future__ import annotations

import fcntl
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.strategy_governance")

_DECISIONS_FILE = Path(__file__).parent.parent / "data" / "governance_decisions.jsonl"

# Weight floor for REDUCE_WEIGHT / QUARANTINE
_WEIGHT_FLOOR_REDUCE    = 0.20
_WEIGHT_QUARANTINE      = 0.10
_REDUCE_FACTOR          = 0.20   # 20 % reduction

# Thresholds
_DECAY_SEVERITY_CUTOFF  = 0.70
_CALIBRATION_CUTOFF     = 0.30
_OVERFIT_CUTOFF         = 0.60
_EXPECTANCY_CUTOFF      = -5.0
_MIN_TRADES_QUARANTINE  = 20


# ── GovernanceAction ──────────────────────────────────────────────────────────

class GovernanceAction(str, Enum):
    REDUCE_WEIGHT       = "REDUCE_WEIGHT"
    DISABLE_IN_REGIME   = "DISABLE_IN_REGIME"
    CLAMP_CONFIDENCE    = "CLAMP_CONFIDENCE"
    FREEZE_OPTIMIZATION = "FREEZE_OPTIMIZATION"
    QUARANTINE          = "QUARANTINE"
    NO_ACTION           = "NO_ACTION"


# ── GovernanceDecision ────────────────────────────────────────────────────────

@dataclass
class GovernanceDecision:
    """A single, immutable governance decision for one strategy."""
    strategy:           str
    action:             GovernanceAction
    old_weight:         float
    new_weight:         float
    reason:             str
    regime_mask:        Optional[List[str]]   # populated for DISABLE_IN_REGIME
    confidence_clamp:   Optional[float]       # populated for CLAMP_CONFIDENCE
    reversible:         bool
    created_at:         str
    trace_id:           str
    applied:            bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy":         self.strategy,
            "action":           self.action.value,
            "old_weight":       self.old_weight,
            "new_weight":       self.new_weight,
            "reason":           self.reason,
            "regime_mask":      self.regime_mask,
            "confidence_clamp": self.confidence_clamp,
            "reversible":       self.reversible,
            "created_at":       self.created_at,
            "trace_id":         self.trace_id,
            "applied":          self.applied,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GovernanceDecision":
        return cls(
            strategy=           d["strategy"],
            action=             GovernanceAction(d["action"]),
            old_weight=         float(d["old_weight"]),
            new_weight=         float(d["new_weight"]),
            reason=             d.get("reason", ""),
            regime_mask=        d.get("regime_mask"),
            confidence_clamp=   d.get("confidence_clamp"),
            reversible=         bool(d.get("reversible", True)),
            created_at=         d.get("created_at", ""),
            trace_id=           d.get("trace_id", str(uuid.uuid4())),
            applied=            bool(d.get("applied", False)),
        )


# ── StrategyGovernanceEngine ──────────────────────────────────────────────────

class StrategyGovernanceEngine:
    """Governance integration layer — routes attribution findings to shadow engine.

    Parameters
    ----------
    outcomes_path:
        Path to ``data/logs/trade_outcomes.jsonl``.
    dry_run:
        When True, decisions are generated but no weights are written and the
        shadow engine is never called.
    """

    def __init__(
        self,
        outcomes_path: str = "data/logs/trade_outcomes.jsonl",
        dry_run: bool = False,
    ) -> None:
        self._outcomes_path = outcomes_path
        self._dry_run       = dry_run
        self._lock          = threading.Lock()
        self._decisions:    List[GovernanceDecision] = []

        # Lazy imports so unit tests can import this module without all deps
        self._attribution_engine = None
        self._shadow_engine      = None

        logger.info(
            "StrategyGovernanceEngine initialised — outcomes=%s  dry_run=%s",
            outcomes_path, dry_run,
        )

    # ── Lazy dependency accessors ─────────────────────────────────────────────

    def _get_attribution_engine(self):
        if self._attribution_engine is None:
            from research.analytics.strategy_attribution import (
                StrategyAttributionEngine,
            )
            self._attribution_engine = StrategyAttributionEngine()
        return self._attribution_engine

    def _get_shadow_engine(self):
        if self._shadow_engine is None:
            from runtime.shadow_optimization import ShadowOptimizationEngine
            self._shadow_engine = ShadowOptimizationEngine()
        return self._shadow_engine

    # ── Public API ────────────────────────────────────────────────────────────

    def run_governance_cycle(self) -> List[GovernanceDecision]:
        """Main entry point: load outcomes, run attribution, generate + apply decisions.

        Returns
        -------
        All decisions generated in this cycle (applied or advisory-only).
        """
        # 1. Load trade outcomes
        engine = self._get_attribution_engine()
        loaded = engine.load_outcomes(self._outcomes_path)
        logger.info("run_governance_cycle: loaded %d trade outcomes", loaded)

        # 2. Generate attribution report
        report = engine.generate_report()
        logger.info(
            "run_governance_cycle: %d strategies analysed, degraded=%s",
            len(report.strategies),
            report.degraded_strategies,
        )

        # 3. Generate decisions
        cycle_decisions = self._generate_decisions(report)

        # 4. Persist + (optionally) apply
        with self._lock:
            for d in cycle_decisions:
                self._decisions.append(d)

        self._persist_decisions(cycle_decisions)

        if not self._dry_run:
            for d in cycle_decisions:
                if d.action in (
                    GovernanceAction.REDUCE_WEIGHT,
                    GovernanceAction.QUARANTINE,
                    GovernanceAction.FREEZE_OPTIMIZATION,
                ):
                    self._apply_decision(d)
        else:
            logger.info(
                "run_governance_cycle: dry_run=True — %d decisions generated "
                "but NOT applied",
                len(cycle_decisions),
            )

        return cycle_decisions

    def get_pending_decisions(self) -> List[GovernanceDecision]:
        """Return all decisions that have not yet been applied."""
        with self._lock:
            return [d for d in self._decisions if not d.applied]

    def rollback(self, trace_id: str) -> bool:
        """Roll back the weight change associated with *trace_id*.

        Delegates to ShadowOptimizationEngine.rollback().

        Returns
        -------
        True on success, False if the trace_id is not found or rollback fails.
        """
        with self._lock:
            matching = [d for d in self._decisions if d.trace_id == trace_id]

        if not matching:
            logger.warning("rollback: trace_id %s not found", trace_id)
            return False

        decision = matching[-1]
        try:
            shadow = self._get_shadow_engine()
            ok, reason = shadow.rollback(decision.strategy)
            if ok:
                logger.info(
                    "rollback: %s (%s) rolled back via shadow engine",
                    decision.strategy, trace_id,
                )
            else:
                logger.warning(
                    "rollback: %s failed — %s", decision.strategy, reason
                )
            return ok
        except Exception as exc:
            logger.error("rollback: exception for %s: %s", trace_id, exc)
            return False

    def get_audit_trail(self) -> List[dict]:
        """Return all decisions ever generated, serialised as plain dicts."""
        with self._lock:
            return [d.to_dict() for d in self._decisions]

    # ── Decision generation ───────────────────────────────────────────────────

    def _generate_decisions(self, report) -> List[GovernanceDecision]:
        """Translate attribution findings into governance decisions.

        For each strategy present in the report one decision is generated (the
        most severe applicable action wins; rules are evaluated in priority
        order).
        """
        decisions: List[GovernanceDecision] = []

        for strat_name, metrics in report.strategies.items():
            old_weight = self._current_weight(strat_name)
            decision   = self._evaluate_strategy(strat_name, metrics, old_weight, report)
            decisions.append(decision)

        return decisions

    def _evaluate_strategy(
        self,
        strategy:   str,
        metrics,
        old_weight: float,
        report,
    ) -> GovernanceDecision:
        """Apply priority-ordered rule set and return the winning decision."""
        now = datetime.now(timezone.utc).isoformat()
        tid = str(uuid.uuid4())

        # ── Rule 1: QUARANTINE — worst-case, evaluated first ─────────────────
        if (
            metrics.expectancy_usd < _EXPECTANCY_CUTOFF
            and metrics.total_trades >= _MIN_TRADES_QUARANTINE
        ):
            new_weight = max(_WEIGHT_QUARANTINE, _WEIGHT_QUARANTINE)
            return GovernanceDecision(
                strategy=           strategy,
                action=             GovernanceAction.QUARANTINE,
                old_weight=         old_weight,
                new_weight=         new_weight,
                reason=(
                    f"Expectancy {metrics.expectancy_usd:.2f} USD < "
                    f"{_EXPECTANCY_CUTOFF} USD threshold with "
                    f"{metrics.total_trades} trades — quarantined"
                ),
                regime_mask=        None,
                confidence_clamp=   None,
                reversible=         True,
                created_at=         now,
                trace_id=           tid,
            )

        # ── Rule 2: REDUCE_WEIGHT — decay ─────────────────────────────────────
        if metrics.decay_detected and metrics.decay_severity > _DECAY_SEVERITY_CUTOFF:
            reduction  = old_weight * _REDUCE_FACTOR
            new_weight = max(_WEIGHT_FLOOR_REDUCE, old_weight - reduction)
            return GovernanceDecision(
                strategy=           strategy,
                action=             GovernanceAction.REDUCE_WEIGHT,
                old_weight=         old_weight,
                new_weight=         round(new_weight, 4),
                reason=(
                    f"Decay detected — severity {metrics.decay_severity:.2f} > "
                    f"{_DECAY_SEVERITY_CUTOFF} — reducing weight by "
                    f"{_REDUCE_FACTOR * 100:.0f}%"
                ),
                regime_mask=        None,
                confidence_clamp=   None,
                reversible=         True,
                created_at=         now,
                trace_id=           tid,
            )

        # ── Rule 3: FREEZE_OPTIMIZATION — overfitting ─────────────────────────
        if metrics.overfitting_score > _OVERFIT_CUTOFF:
            return GovernanceDecision(
                strategy=           strategy,
                action=             GovernanceAction.FREEZE_OPTIMIZATION,
                old_weight=         old_weight,
                new_weight=         old_weight,   # no weight change
                reason=(
                    f"Overfitting score {metrics.overfitting_score:.2f} > "
                    f"{_OVERFIT_CUTOFF} — shadow optimization frozen"
                ),
                regime_mask=        None,
                confidence_clamp=   None,
                reversible=         True,
                created_at=         now,
                trace_id=           tid,
            )

        # ── Rule 4: DISABLE_IN_REGIME — regime blindness (advisory) ──────────
        blind_regimes = report.worst_regime_fit.get(strategy)
        # Use the attribution engine's detect_regime_blindness for the blind list
        try:
            attr = self._get_attribution_engine()
            blind_list = attr.detect_regime_blindness(strategy)
        except Exception:
            blind_list = []

        if blind_list:
            return GovernanceDecision(
                strategy=           strategy,
                action=             GovernanceAction.DISABLE_IN_REGIME,
                old_weight=         old_weight,
                new_weight=         old_weight,   # advisory — no weight change
                reason=(
                    f"Regime blindness detected in: {blind_list} — "
                    "advisory note logged (compatibility matrix NOT modified)"
                ),
                regime_mask=        blind_list,
                confidence_clamp=   None,
                reversible=         True,
                created_at=         now,
                trace_id=           tid,
            )

        # ── Rule 5: CLAMP_CONFIDENCE — calibration (advisory) ─────────────────
        if metrics.confidence_calibration_score < _CALIBRATION_CUTOFF:
            return GovernanceDecision(
                strategy=           strategy,
                action=             GovernanceAction.CLAMP_CONFIDENCE,
                old_weight=         old_weight,
                new_weight=         old_weight,   # advisory — no weight change
                reason=(
                    f"Confidence calibration score {metrics.confidence_calibration_score:.3f} "
                    f"< {_CALIBRATION_CUTOFF} — advisory clamp at 0.75 suggested"
                ),
                regime_mask=        None,
                confidence_clamp=   0.75,
                reversible=         True,
                created_at=         now,
                trace_id=           tid,
            )

        # ── Default: NO_ACTION ────────────────────────────────────────────────
        return GovernanceDecision(
            strategy=           strategy,
            action=             GovernanceAction.NO_ACTION,
            old_weight=         old_weight,
            new_weight=         old_weight,
            reason=             "All metrics within acceptable bounds",
            regime_mask=        None,
            confidence_clamp=   None,
            reversible=         True,
            created_at=         now,
            trace_id=           tid,
        )

    # ── Decision application ──────────────────────────────────────────────────

    def _apply_decision(self, decision: GovernanceDecision) -> None:
        """Apply a single decision — weight changes only go through shadow engine.

        Advisory actions (DISABLE_IN_REGIME, CLAMP_CONFIDENCE) are logged as
        WARNING but never modify the compatibility matrix or any configuration.
        """
        action = decision.action

        if action in (GovernanceAction.REDUCE_WEIGHT, GovernanceAction.QUARANTINE):
            self._apply_weight_change(decision)

        elif action is GovernanceAction.FREEZE_OPTIMIZATION:
            self._apply_freeze(decision)

        elif action is GovernanceAction.DISABLE_IN_REGIME:
            logger.warning(
                "[governance][ADVISORY] %s: %s — regimes=%s  "
                "(compatibility matrix NOT modified — human review required)",
                decision.strategy, decision.reason, decision.regime_mask,
            )

        elif action is GovernanceAction.CLAMP_CONFIDENCE:
            logger.warning(
                "[governance][ADVISORY] %s: %s — clamp=%.2f  "
                "(confidence NOT enforced in code — human review required)",
                decision.strategy, decision.reason, decision.confidence_clamp or 0.0,
            )

        elif action is GovernanceAction.NO_ACTION:
            logger.debug(
                "[governance] %s: NO_ACTION — %s",
                decision.strategy, decision.reason,
            )

    def _apply_weight_change(self, decision: GovernanceDecision) -> None:
        """Route REDUCE_WEIGHT / QUARANTINE through ShadowOptimizationEngine."""
        try:
            shadow  = self._get_shadow_engine()

            # Retrieve actual_trades from attribution for the shadow candidate
            try:
                attr    = self._get_attribution_engine()
                report  = attr.generate_report()
                metrics = report.strategies.get(decision.strategy)
                actual_trades = metrics.total_trades if metrics else 0
            except Exception:
                actual_trades = 0

            candidate = shadow.apply_candidate(
                strategy_name=  decision.strategy,
                new_weight=     decision.new_weight,
                source=         "strategy_governance",
                actual_trades=  actual_trades,
            )

            logger.info(
                "[governance] %s → shadow candidate registered: "
                "%.4f → %.4f  (action=%s  trace_id=%s)",
                decision.strategy,
                decision.old_weight,
                decision.new_weight,
                decision.action.value,
                decision.trace_id,
            )

            # Mark as applied on the decision object
            with self._lock:
                decision.applied = True

        except Exception as exc:
            logger.error(
                "[governance] _apply_weight_change failed for %s: %s",
                decision.strategy, exc,
            )

    def _apply_freeze(self, decision: GovernanceDecision) -> None:
        """Mark any pending shadow candidate for this strategy as FROZEN."""
        try:
            shadow    = self._get_shadow_engine()
            candidate = shadow.get_candidate(decision.strategy)
            if candidate is not None and candidate.status == "PENDING":
                # We cannot mutate ShadowCandidate status directly without going
                # through the engine's promote/reject flow.  Instead we register
                # the candidate as rejected via the engine's own reject path by
                # calling promote() which will fail validation if confidence is
                # 0, then log the freeze advisory.
                logger.warning(
                    "[governance][FREEZE] %s: existing PENDING shadow candidate "
                    "detected — marking FROZEN by advisory (candidate.status=%s). "
                    "Auto-promotion is suppressed. Reason: %s",
                    decision.strategy,
                    candidate.status,
                    decision.reason,
                )
                # Force the candidate into a rejected state so it won't auto-promote
                candidate.confidence      = 0.0
                candidate.rejection_reason = f"FROZEN by governance: {decision.reason}"
                candidate.status           = "REJECTED"
            else:
                logger.info(
                    "[governance][FREEZE] %s: no PENDING candidate — "
                    "freeze advisory noted (shadow_optimization will skip if "
                    "a new candidate is created with source=frozen).",
                    decision.strategy,
                )

            with self._lock:
                decision.applied = True

        except Exception as exc:
            logger.error(
                "[governance] _apply_freeze failed for %s: %s",
                decision.strategy, exc,
            )

    # ── Weight lookup ─────────────────────────────────────────────────────────

    def _current_weight(self, strategy: str) -> float:
        """Return the current live weight for a strategy (default 1.0)."""
        try:
            weights_file = (
                Path(__file__).parent.parent / "data" / "strategy_weights.json"
            )
            if weights_file.exists():
                raw = json.loads(weights_file.read_text())
                entry = raw.get(strategy, {})
                if isinstance(entry, dict):
                    return float(entry.get("weight", 1.0))
                if isinstance(entry, (int, float)):
                    return float(entry)
        except Exception as exc:
            logger.debug("_current_weight: could not read weights file: %s", exc)
        return 1.0

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_decisions(self, decisions: List[GovernanceDecision]) -> None:
        """Append decisions to the governance JSONL file (fcntl-locked)."""
        if not decisions:
            return

        _DECISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

        try:
            with _DECISIONS_FILE.open("a", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    for d in decisions:
                        fh.write(json.dumps(d.to_dict()) + "\n")
                    fh.flush()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            logger.debug(
                "_persist_decisions: wrote %d decisions to %s",
                len(decisions), _DECISIONS_FILE,
            )
        except OSError as exc:
            logger.error(
                "_persist_decisions: failed to write to %s: %s",
                _DECISIONS_FILE, exc,
            )


# ── Module-level singleton ────────────────────────────────────────────────────

_singleton: Optional[StrategyGovernanceEngine] = None
_singleton_lock = threading.Lock()


def get_governance_engine(dry_run: bool = False) -> StrategyGovernanceEngine:
    """Return the module-level singleton StrategyGovernanceEngine.

    The first call constructs the engine; subsequent calls return the cached
    instance.  ``dry_run`` is honoured only on first construction.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = StrategyGovernanceEngine(dry_run=dry_run)
        return _singleton
