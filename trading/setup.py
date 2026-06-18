"""ClawBot — TJR Trade Setup Builder
=====================================
Pure helpers that turn a fired liquidity-sweep `Signal` into a concrete,
SEND-ONLY trade plan: entry, protective stop, and a 1R/2R/3R target ladder.

This module is execution-free by design. Nothing here touches the exchange,
places an order, or imports the executor. The user reviews the formatted setup
on Telegram and executes the trade manually elsewhere.

Stop logic (close-only series):
  The pipeline only has CLOSE prices — no OHLC wicks — so swing levels are
  derived from a recent window of closes (mirroring liquidity_sweep.py). For a
  BUY the stop sits just below the recent swing low; for a SELL just above the
  recent swing high. A small buffer (ATR-of-closes, falling back to a percent)
  is added so price has room beyond the swept level.

Targets:
  risk = |entry - stop|; targets are placed at entry ± 1R / 2R / 3R in the
  trade's direction.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from trading.strategy import Signal, TradeSetup


# ── Tunables ──────────────────────────────────────────────────────────────────

DEFAULT_SWING_LOOKBACK = 20     # closes used to locate the recent swing level
DEFAULT_ATR_PERIOD = 14         # period for the close-based ATR buffer
DEFAULT_STOP_BUFFER_PCT = 0.3   # % fallback buffer when ATR is unavailable
DEFAULT_ATR_BUFFER_MULT = 0.5   # fraction of ATR added beyond the swing level
DEFAULT_TARGET_MULTIPLES = (1.0, 2.0, 3.0)  # 1R / 2R / 3R


# ── Helpers ───────────────────────────────────────────────────────────────────

def _atr_from_closes(closes: Sequence[float], period: int = DEFAULT_ATR_PERIOD) -> Optional[float]:
    """Close-only volatility proxy: mean absolute close-to-close change.

    Returns None if there aren't enough closes to measure it.
    """
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    diffs = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
    if not diffs:
        return None
    return sum(diffs) / len(diffs)


def _swing_levels(closes: Sequence[float], lookback: int = DEFAULT_SWING_LOOKBACK):
    """Return (swing_low, swing_high) over the last `lookback` closes."""
    window = closes[-lookback:] if len(closes) >= lookback else list(closes)
    return min(window), max(window)


def _stop_buffer(closes: Sequence[float], entry: float) -> float:
    """Buffer added beyond the swept swing level.

    Prefers an ATR-of-closes buffer; falls back to a percent-of-entry buffer
    when there isn't enough history for ATR.
    """
    atr = _atr_from_closes(closes)
    if atr is not None and atr > 0:
        return atr * DEFAULT_ATR_BUFFER_MULT
    return abs(entry) * (DEFAULT_STOP_BUFFER_PCT / 100.0)


# ── Core ──────────────────────────────────────────────────────────────────────

def build_trade_setup(
    signal: Signal,
    closes: Sequence[float],
    lookback: int = DEFAULT_SWING_LOOKBACK,
    target_multiples: Sequence[float] = DEFAULT_TARGET_MULTIPLES,
) -> Optional[TradeSetup]:
    """Build a TradeSetup from a fired BUY/SELL Signal and its recent closes.

    Returns None for HOLD signals or when closes are empty — there is nothing
    to plan in those cases.

    entry  = last close
    stop   = swing low (BUY) / swing high (SELL), with a small buffer beyond it
    targets= entry ± 1R / 2R / 3R, where R = |entry - stop|
    """
    if signal.action not in ("BUY", "SELL"):
        return None
    if not closes:
        return None

    entry = float(closes[-1])
    swing_low, swing_high = _swing_levels(closes, lookback)
    buffer = _stop_buffer(closes, entry)

    if signal.action == "BUY":
        stop = swing_low - buffer
        risk = entry - stop
        if risk <= 0:
            # Degenerate (entry at/below the swing low + buffer): fall back to a
            # percent-based stop so risk is always positive and well-defined.
            risk = entry * (DEFAULT_STOP_BUFFER_PCT / 100.0)
            stop = entry - risk
        targets = [entry + risk * m for m in target_multiples]
    else:  # SELL
        stop = swing_high + buffer
        risk = stop - entry
        if risk <= 0:
            risk = entry * (DEFAULT_STOP_BUFFER_PCT / 100.0)
            stop = entry + risk
        targets = [entry - risk * m for m in target_multiples]

    reward_to_risk = float(target_multiples[-1]) if target_multiples else 0.0

    return TradeSetup(
        coin=signal.coin,
        direction=signal.action,
        entry=entry,
        stop=stop,
        targets=targets,
        reward_to_risk=reward_to_risk,
        confidence=signal.confidence,
        reason=signal.reason,
    )


# ── Formatting ────────────────────────────────────────────────────────────────

def format_trade_setup_telegram(setup: TradeSetup, signal: Signal) -> str:
    """Render a clean HTML Telegram message for a trade setup.

    Pure formatting — no I/O. Mirrors the HTML style used elsewhere
    (Signal.to_telegram_message, scheduler reports).
    """
    emoji      = "🟢" if setup.direction == "BUY" else "🔴"
    conf_emoji = "💪" if setup.confidence == "HIGH" else "👍" if setup.confidence == "MEDIUM" else "🤔"
    risk       = abs(setup.entry - setup.stop)

    lines = [
        f"{emoji} <b>TJR Trade Setup — {setup.coin}</b>",
        f"Direction: <code>{setup.direction}</code>",
        f"Confidence: {conf_emoji} <code>{setup.confidence}</code>\n",
        f"📍 <b>Levels</b>",
        f"Entry: <code>{setup.entry:.6f}</code>",
        f"Stop:  <code>{setup.stop:.6f}</code>  <i>(risk {risk:.6f})</i>",
    ]

    for i, target in enumerate(setup.targets, start=1):
        rr = i  # 1R / 2R / 3R by construction
        lines.append(f"T{i}:    <code>{target:.6f}</code>  <i>({rr}R)</i>")

    lines.append(f"\n🎯 Reward:Risk up to <code>{setup.reward_to_risk:.1f}R</code>")
    lines.append(f"\n🧠 <b>Reasoning</b>\n{setup.reason}")
    lines.append(
        "\n<i>⚠️ Send-only setup. ClawBot placed NO order — "
        "you execute this trade manually.</i>"
    )
    return "\n".join(lines)


# ── Logging ───────────────────────────────────────────────────────────────────

def to_jsonl_record(setup: TradeSetup, signal: Signal) -> str:
    """Serialise a setup to a single JSONL line for data/logs/tjr_setups.jsonl."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "coin": setup.coin,
        "direction": setup.direction,
        "entry": setup.entry,
        "stop": setup.stop,
        "targets": list(setup.targets),
        "reward_to_risk": setup.reward_to_risk,
        "confidence": setup.confidence,
        "rsi": round(signal.rsi, 2),
        "reason": setup.reason,
    }
    return json.dumps(record)
