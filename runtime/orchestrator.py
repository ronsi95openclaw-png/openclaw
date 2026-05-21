"""RuntimeOrchestrator — wires all subsystems into a live runtime.

This is the central coordinator that was previously missing.
It connects:
  - BloFinBot strategy signals (source of trading intents)
  - RegimeClassifier (regime compatibility check, advisory)
  - CapitalPreservationEngine (risk scalar, AUTHORITATIVE)
  - IntentPipeline (schema + safety validation, AUTHORITATIVE)
  - ReplayJournal (append-only decision log)
  - Governance layer (kill switch, halt state)

Authority hierarchy (strictly enforced):
  1. Kill switch / Emergency halt        ← supreme authority
  2. CapitalPreservationEngine           ← capital authoritative
  3. IntentPipeline (schema + regime)    ← validates AI outputs
  4. BloFinBot strategy signals          ← advisory, intent source
  5. RegimeClassifier / AI brain         ← advisory only

AI systems NEVER have execution authority.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from runtime.trace import TraceContext, new_trace
from runtime.intent_pipeline import IntentPipeline, TradingIntent, IntentVerdict
from runtime.replay_journal import ReplayJournal

logger = logging.getLogger("openclaw.runtime.orchestrator")


class RuntimeOrchestrator:
    """Central runtime coordinator.

    Instantiate once per process. Call process_signal() whenever the
    trading bot generates a strategy signal.  Returns an IntentVerdict
    that tells the caller whether to proceed with execution.

    Example usage in cryptocom_bot._scan():

        verdict = self._orchestrator.process_signal(
            symbol="BTC-USDT",
            strategy="EMA_CROSS",
            action="long",
            confidence=0.72,
            leverage_requested=3.0,
            size_pct=1.5,
            sl_pct=1.2,
            tp_pct=2.5,
        )
        if verdict.approved:
            # execute at verdict.adjusted_size_pct
        else:
            logger.info("Signal rejected: %s", verdict.reason)
    """

    def __init__(self,
                 journal: Optional[ReplayJournal] = None,
                 capital_engine=None,
                 governance_controls=None,
                 ruflo_advisor=None,
                 intent_ttl_seconds: int = 90):
        self._journal   = journal or ReplayJournal()
        self._capital   = capital_engine
        self._governance = governance_controls
        self._ruflo     = ruflo_advisor   # ADVISORY ONLY — never execution-authoritative
        self._ttl       = intent_ttl_seconds
        self._lock      = threading.Lock()
        self._active    = False

        # Lazily load regime compatibility (advisory)
        self._regime_compat = self._load_regime_compat()

        # Intent pipeline (authoritative)
        self._pipeline = IntentPipeline(
            capital_engine=self._capital,
            regime_compat=self._regime_compat,
        )

        logger.info(
            "RuntimeOrchestrator initialized — capital=%s  governance=%s  "
            "regime_compat=%s  ruflo=%s",
            "wired" if self._capital else "NOT WIRED",
            "wired" if self._governance else "NOT WIRED",
            "wired" if self._regime_compat else "NOT WIRED",
            "wired" if self._ruflo and self._ruflo.is_available() else "NOT WIRED",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._active = True
        logger.info("RuntimeOrchestrator: ACTIVE")

    def stop(self) -> None:
        with self._lock:
            self._active = False
        logger.info("RuntimeOrchestrator: STOPPED")

    def is_active(self) -> bool:
        return self._active

    def process_signal(
        self,
        symbol: str,
        strategy: str,
        action: str,
        confidence: float,
        leverage_requested: float = 3.0,
        size_pct: float = 1.5,
        sl_pct: float = 1.2,
        tp_pct: float = 2.5,
        regime_label: str = "UNKNOWN",
        source: str = "strategy",
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IntentVerdict:
        """Main entry point: validate a strategy signal through all safety gates.

        Returns an IntentVerdict. Caller must check verdict.approved
        before executing anything.

        This method is SYNCHRONOUS and thread-safe.
        """
        # Gate 0: kill switch / governance halt
        if self._is_globally_halted():
            tid = trace_id or str(uuid.uuid4())
            logger.critical("Signal blocked by global HALT  symbol=%s  strategy=%s", symbol, strategy)
            self._journal.record("intent_blocked_global_halt", tid,
                                 {"symbol": symbol, "strategy": strategy, "reason": "global_halt"})
            return IntentVerdict(
                intent_id=str(uuid.uuid4()),
                approved=False,
                reason="Global halt is active — all trading suspended",
                risk_scalar=0.0,
                adjusted_size_pct=0.0,
            )

        if not self._active:
            return IntentVerdict(
                intent_id=str(uuid.uuid4()),
                approved=False,
                reason="Orchestrator not active",
                risk_scalar=0.0,
                adjusted_size_pct=0.0,
            )

        # Build trace context
        tid = trace_id or str(uuid.uuid4())
        ctx = TraceContext(
            trace_id=tid,
            parent_id=None,
            source=source,
            symbol=symbol,
            strategy=strategy,
        )

        # Record signal in journal
        self._journal.record_signal(tid, symbol, strategy, action, confidence)

        # Ruflo advisory lookup (never blocks execution, purely informational)
        ruflo_conf_adj = 0.0
        if self._ruflo and self._ruflo.is_available():
            try:
                advice = self._ruflo.pre_trade_advice(
                    symbol=symbol, strategy=strategy, action=action,
                    confidence=confidence, regime=regime_label,
                )
                ruflo_conf_adj = advice.confidence_adj
                self._journal.record("ruflo_advice", tid, {
                    "symbol": symbol, "strategy": strategy, "action": action,
                    "similar_wins": advice.similar_wins,
                    "similar_losses": advice.similar_losses,
                    "win_rate": advice.win_rate,
                    "avg_pnl": advice.avg_pnl,
                    "confidence_adj": advice.confidence_adj,
                    "swarm_summary": advice.swarm_summary[:200] if advice.swarm_summary else "",
                    "latency_ms": advice.latency_ms,
                })
                if advice.swarm_summary:
                    logger.debug("Ruflo swarm: %s | %s", symbol, advice.swarm_summary[:120])
            except Exception as exc:
                logger.debug("Ruflo advisory error (non-fatal): %s", exc)

        # Build intent
        now = datetime.now(timezone.utc)
        intent = TradingIntent(
            symbol=symbol,
            strategy=strategy,
            action=action,
            confidence=confidence,
            leverage_requested=leverage_requested,
            size_pct=size_pct,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            regime_label=regime_label,
            source=source,
            trace_id=tid,
            generated_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
            metadata=metadata or {},
        )

        # Run through intent pipeline (schema + regime + capital)
        verdict = self._pipeline.validate(intent)

        # Journal the verdict
        self._journal.record_intent_verdict(
            tid, intent.intent_id, verdict.approved,
            verdict.reason, verdict.risk_scalar, verdict.adjusted_size_pct,
        )

        return verdict

    def update_capital_state(self, equity: float,
                              trade_pnl: Optional[float] = None) -> None:
        """Feed equity update to capital preservation engine."""
        if self._capital is None:
            return
        old_state = self._capital.get_state().name
        self._capital.update(equity, trade_pnl)
        new_state = self._capital.get_state().name
        if old_state != new_state:
            self._journal.record_capital_state(
                trace_id=None,
                old_state=old_state,
                new_state=new_state,
                trigger="equity_update",
                equity=equity,
            )
            logger.warning(
                "Capital state transition: %s → %s  equity=%.2f",
                old_state, new_state, equity,
            )

    def record_trade_outcome(
        self,
        symbol:   str,
        strategy: str,
        pnl:      float,
        regime:   str = "UNKNOWN",
        action:   str = "UNKNOWN",
        win:      bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist trade outcome in Ruflo memory for future advisory lookups.

        Call this from cryptocom_bot._close_position() after each trade settles.
        """
        if self._ruflo and self._ruflo.is_available():
            self._ruflo.record_outcome(
                symbol=symbol, strategy=strategy, pnl=pnl,
                regime=regime, action=action, win=win, metadata=metadata,
            )

    def classify_regime(self, symbol: str, candles: list) -> Optional[str]:
        """Run regime classification (advisory). Returns label string."""
        try:
            from research.regimes.classifier import RegimeClassifier
            clf = RegimeClassifier()
            state = clf.classify(candles)
            self._journal.record_regime(
                trace_id=None,
                symbol=symbol,
                label=state.label,
                adx=state.adx,
                atr_ratio=state.atr_ratio,
            )
            return state.label
        except Exception as exc:
            logger.debug("Regime classification unavailable: %s", exc)
            return None

    def get_status(self) -> Dict[str, Any]:
        """Return full orchestrator status for dashboard / health checks."""
        capital_status: Dict[str, Any] = {}
        if self._capital is not None:
            try:
                capital_status = self._capital.get_status_dict()
            except Exception:
                capital_status = {"error": "unavailable"}

        journal_stats = self._journal.get_stats()
        wiring = self._get_wiring_report()

        return {
            "active":          self._active,
            "globally_halted": self._is_globally_halted(),
            "capital":         capital_status,
            "journal":         journal_stats,
            "wiring":          wiring,
            "intent_ttl_s":    self._ttl,
        }

    def get_wiring_report(self) -> Dict[str, Any]:
        return self._get_wiring_report()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _is_globally_halted(self) -> bool:
        if self._governance is None:
            return False
        try:
            return self._governance.is_emergency_halted()
        except Exception:
            return False

    def _load_regime_compat(self):
        """Load strategy/regime compatibility module (advisory)."""
        try:
            from research.regimes import strategy_compatibility
            return strategy_compatibility
        except ImportError:
            logger.debug("strategy_compatibility not available — regime checks disabled")
            return None

    def _get_wiring_report(self) -> Dict[str, Any]:
        """Reports which subsystems are actually wired vs. missing."""
        ruflo_status = {}
        if self._ruflo:
            ruflo_status = self._ruflo.get_status()
        return {
            "capital_preservation": self._capital is not None,
            "governance":           self._governance is not None,
            "regime_compat":        self._regime_compat is not None,
            "journal":              True,   # always wired
            "intent_pipeline":      True,   # always wired
            "ruflo_advisory":       bool(self._ruflo and self._ruflo.is_available()),
            "ruflo_status":         ruflo_status,
        }


def build_orchestrator(
    journal_path: str = "data/replay_journal.jsonl",
    capital_config: Optional[Dict[str, Any]] = None,
    with_governance: bool = False,
) -> RuntimeOrchestrator:
    """Factory: build a fully-wired RuntimeOrchestrator.

    Loads subsystems defensively — missing modules degrade gracefully.
    """
    journal = ReplayJournal(path=journal_path)

    # Capital preservation (authoritative)
    capital_engine = None
    try:
        from risk.capital_preservation import CapitalPreservationEngine
        cfg = capital_config or {}
        capital_engine = CapitalPreservationEngine(**cfg)
        logger.info("CapitalPreservationEngine loaded")
    except Exception as exc:
        logger.warning("CapitalPreservationEngine unavailable: %s", exc)

    # Governance (kill switch / halt)
    governance = None
    if with_governance:
        try:
            from governance.emergency_controls import EmergencyControls
            governance = EmergencyControls()
            logger.info("EmergencyControls loaded")
        except Exception as exc:
            logger.warning("EmergencyControls unavailable: %s", exc)

    # Ruflo advisory (optional — degrades gracefully when Node.js unavailable)
    ruflo_advisor = None
    try:
        from runtime.ruflo_agent import RufloAdvisor
        ruflo_advisor = RufloAdvisor()
        ruflo_advisor.start()   # non-blocking; logs warning if Ruflo not installed
    except Exception as exc:
        logger.debug("RufloAdvisor unavailable (non-fatal): %s", exc)

    orch = RuntimeOrchestrator(
        journal=journal,
        capital_engine=capital_engine,
        governance_controls=governance,
        ruflo_advisor=ruflo_advisor,
    )
    orch.start()
    return orch
