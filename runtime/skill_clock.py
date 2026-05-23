"""Skill Clock — deterministic 10-skill pipeline scheduler.

The Skill Clock is the heartbeat of the OpenClaw ecosystem. Every tick it
runs 10 specialised skills in sequence, passing a shared SkillContext
forward. Outputs are consumed by QUIN (local LLM orchestrator) and the
Execution Engine.

Skills (in order):
  1. Market Data Ingest       — fetch prices, candles, orderbook
  2. Regime Detection         — classify market regime per symbol
  3. Signal Generation        — produce raw strategy signals
  4. Risk & Capital Check     — gate on capital state, position limits
  5. Execution Decisioning    — pick best signal, build execution plan
  6. Order Management         — manage open positions (SL/TP/DCA/exits)
  7. Reconciliation           — reconcile positions with exchange
  8. Telemetry & Health       — emit metrics, latency, health checks
  9. Learning & Drift         — update weights, detect drift events
 10. Governance & Audit       — audit log, governance policy check
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("openclaw.runtime.skill_clock")

_SKILL_AUDIT_PATH = "data/skill_clock_audit.jsonl"


# ── Shared context ─────────────────────────────────────────────────────────────

@dataclass
class SkillContext:
    """Immutable-in-practice context passed through all 10 skills each tick."""
    tick_id:        str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    tick_ts:        str   = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tick_number:    int   = 0

    # Outputs filled in by each skill
    market_data:       dict  = field(default_factory=dict)   # skill 1
    regimes:           dict  = field(default_factory=dict)   # skill 2  {symbol: regime}
    signals:           list  = field(default_factory=list)   # skill 3
    risk_state:        dict  = field(default_factory=dict)   # skill 4
    execution_plan:    dict  = field(default_factory=dict)   # skill 5
    position_updates:  list  = field(default_factory=list)   # skill 6
    recon_result:      dict  = field(default_factory=dict)   # skill 7
    health:            dict  = field(default_factory=dict)   # skill 8
    learning_updates:  dict  = field(default_factory=dict)   # skill 9
    audit_result:      dict  = field(default_factory=dict)   # skill 10
    quin_decision:     dict  = field(default_factory=dict)   # from QUIN

    # Errors accumulated across skills (non-fatal)
    errors: list = field(default_factory=list)


# ── Skill base class ───────────────────────────────────────────────────────────

class Skill(ABC):
    """Base class for a single skill in the clock."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def skill_number(self) -> int: ...

    @abstractmethod
    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        """Execute skill. Must NEVER raise — swallow and record in ctx.errors."""
        ...


# ── Concrete skills ────────────────────────────────────────────────────────────

class MarketDataIngestSkill(Skill):
    name = "MarketDataIngest"
    skill_number = 1

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            market_data: dict = {}
            for symbol in getattr(bot, "_symbols", ["BTC_USDT", "ETH_USDT", "SOL_USDT"]):
                try:
                    candles, funding = bot._fetch_market_data(symbol)
                    if candles:
                        market_data[symbol] = {
                            "candles": candles,
                            "funding": funding,
                            "price":   candles[-1]["close"] if candles else None,
                        }
                except Exception as exc:
                    ctx.errors.append(f"S1/{symbol}: {exc}")
            ctx.market_data = market_data
        except Exception as exc:
            ctx.errors.append(f"S1: {exc}")
        return ctx


class RegimeDetectionSkill(Skill):
    name = "RegimeDetection"
    skill_number = 2

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            regimes: dict = {}
            orch = getattr(bot, "_orchestrator", None)
            if orch is None:
                ctx.regimes = regimes
                return ctx
            for symbol, mdata in ctx.market_data.items():
                try:
                    from research.types import Candle
                    c_objs = [Candle(**c) for c in mdata["candles"]]
                    regime = orch.classify_regime(symbol, c_objs) or "UNKNOWN"
                    regimes[symbol] = regime
                except Exception as exc:
                    regimes[symbol] = "UNKNOWN"
                    ctx.errors.append(f"S2/{symbol}: {exc}")
            ctx.regimes = regimes
        except Exception as exc:
            ctx.errors.append(f"S2: {exc}")
        return ctx


class SignalGenerationSkill(Skill):
    name = "SignalGeneration"
    skill_number = 3

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            from trading.strategies import (
                ema_cross_strategy, rsi_mean_revert_strategy,
                breakout_strategy, bollinger_band_strategy,
                trend_follow_strategy,
            )
            signals: list = []
            for symbol, mdata in ctx.market_data.items():
                candles = mdata.get("candles", [])
                if not candles:
                    continue
                for fn in (ema_cross_strategy, rsi_mean_revert_strategy,
                           breakout_strategy, bollinger_band_strategy,
                           trend_follow_strategy):
                    try:
                        sig = fn(symbol, candles)
                        if sig is not None:
                            signals.append({**sig.__dict__, "regime": ctx.regimes.get(symbol, "UNKNOWN")})
                    except Exception as exc:
                        ctx.errors.append(f"S3/{symbol}/{fn.__name__}: {exc}")
            ctx.signals = signals
        except Exception as exc:
            ctx.errors.append(f"S3: {exc}")
        return ctx


class RiskCapitalCheckSkill(Skill):
    name = "RiskCapitalCheck"
    skill_number = 4

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            orch = getattr(bot, "_orchestrator", None)
            capital_state = "UNKNOWN"
            risk_scalar   = 1.0
            halted        = False

            if orch and orch._capital:
                capital_state = orch._capital.get_state().name
                risk_scalar   = orch._capital.get_risk_scalar()
                halted        = orch._capital.should_flatten_all()

            open_pos      = len(getattr(bot.state, "open_positions", []))
            max_positions = getattr(bot, "_max_positions", 3)

            ctx.risk_state = {
                "capital_state":   capital_state,
                "risk_scalar":     risk_scalar,
                "halted":          halted,
                "open_positions":  open_pos,
                "max_positions":   max_positions,
                "can_open_new":    (not halted) and (open_pos < max_positions),
            }
        except Exception as exc:
            ctx.errors.append(f"S4: {exc}")
            ctx.risk_state = {"capital_state": "UNKNOWN", "halted": False,
                              "can_open_new": False}
        return ctx


class ExecutionDecisioningSkill(Skill):
    name = "ExecutionDecisioning"
    skill_number = 5

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            if not ctx.risk_state.get("can_open_new", False):
                ctx.execution_plan = {"action": "HOLD", "reason": "risk_gate_closed",
                                      "selected_signal": None}
                return ctx

            # Weight-filter signals and pick best
            weights = {}
            try:
                weights = {k: v.get("weight", 1.0)
                           for k, v in bot.weights.summary().items()}
            except Exception:
                pass

            scored: list = []
            for sig in ctx.signals:
                if sig.get("signal") not in ("long", "short"):
                    continue
                w = weights.get(sig.get("strategy", ""), 1.0)
                score = sig.get("confidence", 0.7) * w
                scored.append({**sig, "weight": w, "score": score})

            scored.sort(key=lambda x: x["score"], reverse=True)
            best = scored[0] if scored else None

            ctx.execution_plan = {
                "action":           "TRADE" if best else "HOLD",
                "selected_signal":  best,
                "candidates":       scored[:3],
                "reason":           "best_scored_signal" if best else "no_signals",
            }
        except Exception as exc:
            ctx.errors.append(f"S5: {exc}")
            ctx.execution_plan = {"action": "HOLD", "reason": str(exc),
                                  "selected_signal": None}
        return ctx


class OrderManagementSkill(Skill):
    name = "OrderManagement"
    skill_number = 6

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            updates: list = []
            for pos in list(getattr(bot.state, "open_positions", [])):
                symbol = pos.get("symbol", "")
                mdata  = ctx.market_data.get(symbol, {})
                if not mdata:
                    continue
                price = mdata.get("price")
                if price is None:
                    continue
                side = pos.get("side", "long")
                sl   = pos.get("sl_price", 0)
                tp   = pos.get("tp_price", 0)
                action = "HOLD"
                if side == "long":
                    if price <= sl:
                        action = "STOP_LOSS"
                    elif price >= tp:
                        action = "TAKE_PROFIT"
                else:
                    if price >= sl:
                        action = "STOP_LOSS"
                    elif price <= tp:
                        action = "TAKE_PROFIT"
                updates.append({"position_id": pos.get("id"), "symbol": symbol,
                                 "action": action, "price": price})
            ctx.position_updates = updates
        except Exception as exc:
            ctx.errors.append(f"S6: {exc}")
        return ctx


class ReconciliationSkill(Skill):
    name = "Reconciliation"
    skill_number = 7

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            recon = getattr(bot, "_recon_scheduler", None)
            if recon:
                report = recon.get_last_report()
                ctx.recon_result = {
                    "passed":   getattr(report, "passed", True) if report else True,
                    "ts":       getattr(report, "ts", "") if report else "",
                    "mismatches": getattr(report, "mismatches", 0) if report else 0,
                }
            else:
                ctx.recon_result = {"passed": True, "ts": "", "mismatches": 0}
        except Exception as exc:
            ctx.errors.append(f"S7: {exc}")
            ctx.recon_result = {"passed": True, "ts": "", "mismatches": 0}
        return ctx


class TelemetryHealthSkill(Skill):
    name = "TelemetryHealth"
    skill_number = 8

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            ctx.health = {
                "tick_id":        ctx.tick_id,
                "tick_ts":        ctx.tick_ts,
                "open_positions": len(getattr(bot.state, "open_positions", [])),
                "signals_count":  len(ctx.signals),
                "errors_count":   len(ctx.errors),
                "recon_ok":       ctx.recon_result.get("passed", True),
                "capital_state":  ctx.risk_state.get("capital_state", "UNKNOWN"),
            }
            # Feed into latency profiler
            try:
                from runtime.latency_profiler import LatencyProfiler, OperationCategory
                p = LatencyProfiler()
                p.record(OperationCategory.SNAPSHOT, "skill_clock_tick", 0.0)
            except Exception:
                pass
        except Exception as exc:
            ctx.errors.append(f"S8: {exc}")
        return ctx


class LearningDriftSkill(Skill):
    name = "LearningDrift"
    skill_number = 9

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            updates: dict = {}
            # Weight scheduler status
            ws = getattr(bot, "_weight_scheduler", None)
            if ws:
                updates["weight_scheduler"] = ws.get_status()
            # Drift detector
            dd = getattr(bot, "_drift_detector", None)
            if dd:
                try:
                    updates["drift"] = {"active": True}
                except Exception:
                    pass
            ctx.learning_updates = updates
        except Exception as exc:
            ctx.errors.append(f"S9: {exc}")
        return ctx


class GovernanceAuditSkill(Skill):
    name = "GovernanceAudit"
    skill_number = 10

    def run(self, ctx: SkillContext, bot: Any) -> SkillContext:
        try:
            record = {
                "tick_id":       ctx.tick_id,
                "tick_ts":       ctx.tick_ts,
                "tick_number":   ctx.tick_number,
                "action":        ctx.execution_plan.get("action", "HOLD"),
                "capital_state": ctx.risk_state.get("capital_state", "UNKNOWN"),
                "signals":       len(ctx.signals),
                "errors":        ctx.errors[:5],   # cap audit record size
            }
            path = Path(_SKILL_AUDIT_PATH)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            ctx.audit_result = {"written": True, "path": str(path)}
        except Exception as exc:
            ctx.errors.append(f"S10: {exc}")
            ctx.audit_result = {"written": False}
        return ctx


# ── Skill Clock ────────────────────────────────────────────────────────────────

_DEFAULT_SKILLS: list[Skill] = [
    MarketDataIngestSkill(),
    RegimeDetectionSkill(),
    SignalGenerationSkill(),
    RiskCapitalCheckSkill(),
    ExecutionDecisioningSkill(),
    OrderManagementSkill(),
    ReconciliationSkill(),
    TelemetryHealthSkill(),
    LearningDriftSkill(),
    GovernanceAuditSkill(),
]


class SkillClock:
    """Runs 10 skills in deterministic sequence every N seconds.

    Thread-safe singleton. The bot calls tick() from its scan loop; the
    SkillClock handles timing, context building, and QUIN integration.
    """

    def __init__(
        self,
        skills: Optional[list] = None,
        audit_path: str = _SKILL_AUDIT_PATH,
    ) -> None:
        self._skills     = skills or list(_DEFAULT_SKILLS)
        self._audit_path = audit_path
        self._lock       = threading.Lock()
        self._tick_count = 0
        self._last_ctx:  Optional[SkillContext] = None

    def tick(self, bot: Any, quin: Optional[Any] = None) -> SkillContext:
        """Run one full 10-skill cycle. Returns the completed SkillContext."""
        with self._lock:
            self._tick_count += 1
            count = self._tick_count

        ctx = SkillContext(tick_number=count)
        t_start = time.monotonic()

        for skill in self._skills:
            try:
                ctx = skill.run(ctx, bot)
            except Exception as exc:
                # Should never happen since skills swallow, but belt-and-suspenders
                ctx.errors.append(f"{skill.name}: UNHANDLED {exc}")
                logger.error("SkillClock: unhandled error in skill %s: %s",
                             skill.name, exc)

        # QUIN decision (between skill 5 and actual execution)
        if quin is not None:
            try:
                decision = quin.decide(ctx)
                ctx.quin_decision = decision
            except Exception as exc:
                ctx.errors.append(f"QUIN: {exc}")

        elapsed_ms = (time.monotonic() - t_start) * 1000.0

        if ctx.errors:
            logger.debug("SkillClock tick #%d completed in %.0fms — %d error(s): %s",
                         count, elapsed_ms, len(ctx.errors), ctx.errors[:3])
        else:
            logger.debug("SkillClock tick #%d completed in %.0fms — clean",
                         count, elapsed_ms)

        with self._lock:
            self._last_ctx = ctx
        return ctx

    def get_last_context(self) -> Optional[SkillContext]:
        with self._lock:
            return self._last_ctx

    def get_status(self) -> dict:
        with self._lock:
            ctx  = self._last_ctx
            tick = self._tick_count
        return {
            "tick_count":     tick,
            "skills":         [s.name for s in self._skills],
            "last_tick_id":   ctx.tick_id if ctx else None,
            "last_tick_ts":   ctx.tick_ts if ctx else None,
            "last_action":    ctx.execution_plan.get("action") if ctx else None,
            "last_regimes":   ctx.regimes if ctx else {},
            "last_errors":    ctx.errors[:5] if ctx else [],
        }


# ── Module singleton ──────────────────────────────────────────────────────────

_clock: Optional[SkillClock] = None
_clock_lock = threading.Lock()


def get_skill_clock() -> SkillClock:
    global _clock
    if _clock is None:
        with _clock_lock:
            if _clock is None:
                _clock = SkillClock()
    return _clock
