"""Ruflo Advisory Agent — trading-specific wrapper around RufloBridge.

All outputs from this module are ADVISORY ONLY. They flow into the
IntentPipeline as additional signals, never as execution-authoritative
decisions. The CapitalPreservationEngine and kill switch layer supersede
any advice returned here.

Usage:
    advisor = RufloAdvisor()
    advisor.start()

    # Before a trade
    advice = advisor.pre_trade_advice("BTC-USDT", "EMA_CROSS", "LONG",
                                      confidence=0.72, regime="TRENDING_BULL")

    # After a trade settles
    advisor.record_outcome("BTC-USDT", "EMA_CROSS", pnl=42.5,
                           regime="TRENDING_BULL", win=True)

    advisor.stop()
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.ruflo_agent")


@dataclass
class RufloAdvice:
    """Advisory output from Ruflo — never execution authoritative."""
    symbol:          str
    strategy:        str
    action:          str
    similar_wins:    int   = 0
    similar_losses:  int   = 0
    win_rate:        float = 0.0
    avg_pnl:         float = 0.0
    swarm_summary:   str   = ""
    confidence_adj:  float = 0.0   # advisory confidence delta (−1.0 to +1.0)
    available:       bool  = False  # False when Ruflo bridge not running
    latency_ms:      float = 0.0


class RufloAdvisor:
    """Advisory-only Ruflo integration for OpenClaw.

    Uses Ruflo's HNSW memory to recall similar historical setups and
    optionally runs a swarm analysis. Results are returned as RufloAdvice
    and are NEVER used to bypass the IntentPipeline.
    """

    def __init__(
        self,
        enabled:      bool = None,   # default: RUFLO_ENABLED env var
        swarm_on:     bool = None,   # default: RUFLO_SWARM_ENABLED env var
        memory_limit: int  = 5,
    ):
        self._enabled     = enabled  if enabled  is not None else _env_bool("RUFLO_ENABLED", True)
        self._swarm_on    = swarm_on if swarm_on is not None else _env_bool("RUFLO_SWARM_ENABLED", False)
        self._mem_limit   = memory_limit
        self._bridge      = None
        self._started     = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start Ruflo bridge subprocess. Returns True if bridge is live."""
        if not self._enabled:
            logger.info("RufloAdvisor: disabled via RUFLO_ENABLED=false")
            return False
        try:
            from runtime.ruflo_bridge import get_bridge
            self._bridge = get_bridge()
            ok = self._bridge.start()
            self._started = ok
            if ok:
                logger.info("RufloAdvisor: bridge live — %d tools available",
                            len(self._bridge.available_tools()))
            else:
                logger.warning("RufloAdvisor: bridge unavailable (Ruflo not installed)")
            return ok
        except Exception as exc:
            logger.warning("RufloAdvisor: could not start bridge: %s", exc)
            return False

    def stop(self) -> None:
        if self._bridge and self._started:
            self._bridge.stop()
        self._started = False

    def is_available(self) -> bool:
        return self._started and self._bridge is not None and self._bridge.is_available()

    # ── Main advisory interface ───────────────────────────────────────────────

    def pre_trade_advice(
        self,
        symbol:     str,
        strategy:   str,
        action:     str,            # "LONG" | "SHORT" | "CLOSE"
        confidence: float,
        regime:     str = "UNKNOWN",
        extra_ctx:  Dict[str, Any] = None,
    ) -> RufloAdvice:
        """Look up similar historical patterns and optionally run swarm analysis.

        Returns RufloAdvice. When Ruflo is unavailable, returns a zero-impact
        advice object so callers don't need to check availability.
        """
        t0 = time.monotonic()
        base = RufloAdvice(symbol=symbol, strategy=strategy, action=action)

        if not self.is_available():
            return base

        try:
            query = _build_memory_query(symbol, strategy, action, regime, confidence)
            memories = self._bridge.memory_search(query, limit=self._mem_limit)
            base = _parse_memories(memories, base)

            if self._swarm_on and memories:
                ctx = {
                    "symbol":     symbol,
                    "strategy":   strategy,
                    "action":     action,
                    "regime":     regime,
                    "confidence": confidence,
                    "similar_setups": len(memories),
                    **(extra_ctx or {}),
                }
                summary = self._bridge.swarm_analyze(
                    task=f"Evaluate {action} {symbol} via {strategy} in {regime} regime",
                    context=ctx,
                )
                base.swarm_summary = summary or ""

            base.available   = True
            base.latency_ms  = (time.monotonic() - t0) * 1000
            return base

        except Exception as exc:
            logger.warning("RufloAdvisor.pre_trade_advice error: %s", exc)
            base.latency_ms = (time.monotonic() - t0) * 1000
            return base

    def record_outcome(
        self,
        symbol:   str,
        strategy: str,
        pnl:      float,
        regime:   str   = "UNKNOWN",
        action:   str   = "UNKNOWN",
        win:      bool  = False,
        metadata: Dict[str, Any] = None,
    ) -> None:
        """Persist trade outcome in Ruflo HNSW memory for future lookups."""
        if not self.is_available():
            return
        try:
            key = f"{symbol}:{strategy}:{action}:{regime}:{int(time.time())}"
            narrative = (metadata or {}).get("narrative", "")
            signal_reason = (metadata or {}).get("signal_reason", "")
            content = (
                f"{symbol} {strategy} {action} regime={regime} "
                f"pnl={pnl:.4f} win={win}"
                + (f" | {signal_reason}" if signal_reason else "")
                + (f" | {narrative}" if narrative else "")
            )
            self._bridge.memory_store(
                key=key,
                content=content,
                metadata={
                    "symbol":        symbol,
                    "strategy":      strategy,
                    "action":        action,
                    "regime":        regime,
                    "pnl":           pnl,
                    "win":           win,
                    "signal_reason": signal_reason,
                    "narrative":     narrative,
                    **(metadata or {}),
                },
            )
        except Exception as exc:
            logger.warning("RufloAdvisor.record_outcome error: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        status = {
            "enabled":   self._enabled,
            "started":   self._started,
            "available": self.is_available(),
            "swarm_on":  self._swarm_on,
        }
        if self._bridge:
            status["bridge"] = self._bridge.get_status()
        return status


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_memory_query(
    symbol: str, strategy: str, action: str, regime: str, confidence: float
) -> str:
    conf_band = "HIGH" if confidence >= 0.75 else "MED" if confidence >= 0.60 else "LOW"
    return f"{symbol} {strategy} {action} {regime} {conf_band}"


def _parse_memories(memories: List[Dict[str, Any]], advice: RufloAdvice) -> RufloAdvice:
    """Extract win/loss stats from memory recall results."""
    if not memories:
        return advice

    wins = losses = 0
    pnl_sum = 0.0
    for m in memories:
        # Metadata may be nested under "metadata" key or flat
        meta = m.get("metadata", m)
        if isinstance(meta, dict):
            w = meta.get("win")
            if w is True:
                wins += 1
            elif w is False:
                losses += 1
            pnl_sum += float(meta.get("pnl", 0.0))
        # Also try parsing content string
        elif isinstance(m.get("content"), str):
            c = m["content"]
            if "win=True" in c:
                wins += 1
            elif "win=False" in c:
                losses += 1

    total = wins + losses
    advice.similar_wins   = wins
    advice.similar_losses = losses
    advice.win_rate       = wins / total if total else 0.0
    advice.avg_pnl        = pnl_sum / len(memories) if memories else 0.0

    # Advisory confidence adjustment: ±0.10 based on historical win rate
    if total >= 3:
        if advice.win_rate >= 0.65:
            advice.confidence_adj = +0.05
        elif advice.win_rate <= 0.35:
            advice.confidence_adj = -0.05

    return advice


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


# ── Singleton ─────────────────────────────────────────────────────────────────

_advisor: Optional[RufloAdvisor] = None


def get_advisor() -> RufloAdvisor:
    """Return (or create) the shared RufloAdvisor singleton."""
    global _advisor
    if _advisor is None:
        _advisor = RufloAdvisor()
    return _advisor
