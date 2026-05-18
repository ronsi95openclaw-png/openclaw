"""BloFin multi-strategy engine with self-learning weight adjustment.

Four strategies run in parallel on each scan:
  EMA_CROSS      — EMA 9/21 crossover on 15m candles
  RSI_MEAN_REVERT — fade RSI extremes (<28 / >72)
  BREAKOUT       — 20-period high/low with volume confirmation
  FUNDING_ARB    — trend-following biased by funding rate

After each strategy closes ≥3 trades the engine adjusts its weight
(0.2× → 2.0×) based on win rate — winners get more capital allocation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("clawbot.trading.blofin_strategies")

_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "blofin_weights.json"

STRATEGIES = ["EMA_CROSS", "RSI_MEAN_REVERT", "BREAKOUT", "FUNDING_ARB"]


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class StrategySignal:
    strategy:   str
    symbol:     str
    action:     str     # "long" | "short" | "hold"
    confidence: float   # 0.0–1.0
    reason:     str
    sl_pct:     float   # stop-loss % distance from entry
    tp_pct:     float   # take-profit % distance from entry


@dataclass
class StrategyStats:
    trades: int   = 0
    wins:   int   = 0
    losses: int   = 0
    weight: float = 1.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades) if self.trades > 0 else 0.5

    def update_weight(self) -> None:
        if self.trades < 3:
            return
        wr     = self.win_rate
        new_w  = 0.2 + (wr ** 1.5) * 1.8   # 0.2 at 0% WR → 2.0 at 100% WR
        self.weight = round(max(0.2, min(2.0, new_w)), 3)


# ── Technical indicators ──────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError(f"EMA needs {period} values, got {len(values)}")
    k   = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append(v * k + ema[-1] * (1.0 - k))
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        raise ValueError(f"RSI needs {period+1} closes")
    deltas   = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains    = [d if d > 0 else 0.0 for d in deltas]
    losses   = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period])  / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i])  / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _atr(candles: list[dict], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# ── Strategy implementations ──────────────────────────────────────────────────

def ema_cross_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """EMA 9 / 21 crossover — fires on a fresh cross, 15m candles."""
    closes = [c["close"] for c in candles]
    _hold  = lambda r: StrategySignal("EMA_CROSS", symbol, "hold", 0.0, r, 1.2, 2.5)

    if len(closes) < 25:
        return _hold("Insufficient data")

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    curr9, prev9   = ema9[-1],  ema9[-2]
    curr21, prev21 = ema21[-1], ema21[-2]

    if prev9 <= prev21 and curr9 > curr21:
        return StrategySignal("EMA_CROSS", symbol, "long",  0.78,
                              f"EMA9 {curr9:.2f} crossed above EMA21 {curr21:.2f}", 1.2, 2.5)
    if prev9 >= prev21 and curr9 < curr21:
        return StrategySignal("EMA_CROSS", symbol, "short", 0.78,
                              f"EMA9 {curr9:.2f} crossed below EMA21 {curr21:.2f}", 1.2, 2.5)

    gap = abs(curr9 - curr21) / curr21 * 100
    return _hold(f"No crossover — spread {gap:.2f}%")


def rsi_mean_revert_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """RSI mean-reversion: buy <28, sell >72."""
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl, tp: StrategySignal("RSI_MEAN_REVERT", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 20:
        return _hold("Insufficient data", 1.5, 3.0)

    rsi   = _rsi(closes, 14)
    price = closes[-1]
    atr   = _atr(candles[-15:], 14)
    sl    = max(0.5, (atr / price * 100) * 1.5) if price > 0 else 1.5
    tp    = sl * 2.0

    if rsi < 28:
        conf = 0.92 if rsi < 22 else 0.72
        return StrategySignal("RSI_MEAN_REVERT", symbol, "long",  conf,
                              f"RSI oversold {rsi:.1f} — mean-reversion long", sl, tp)
    if rsi > 72:
        conf = 0.92 if rsi > 78 else 0.72
        return StrategySignal("RSI_MEAN_REVERT", symbol, "short", conf,
                              f"RSI overbought {rsi:.1f} — mean-reversion short", sl, tp)

    return _hold(f"RSI neutral {rsi:.1f}", sl, tp)


def breakout_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """20-period high/low breakout with volume confirmation."""
    _hold = lambda r: StrategySignal("BREAKOUT", symbol, "hold", 0.0, r, 1.0, 3.5)

    if len(candles) < 22:
        return _hold("Insufficient data")

    lookback   = candles[-21:-1]
    curr       = candles[-1]
    ph         = max(c["high"]   for c in lookback)
    pl         = min(c["low"]    for c in lookback)
    avg_vol    = sum(c["volume"] for c in lookback) / len(lookback)
    vol_ratio  = curr["volume"] / avg_vol if avg_vol > 0 else 1.0
    vol_ok     = vol_ratio > 1.2

    if curr["close"] > ph:
        conf   = 0.88 if vol_ok else 0.56
        reason = f"Broke 20p high {ph:.2f}" + (" ✓ volume" if vol_ok else " — weak vol")
        return StrategySignal("BREAKOUT", symbol, "long",  conf, reason, 1.0, 3.5)

    if curr["close"] < pl:
        conf   = 0.88 if vol_ok else 0.56
        reason = f"Broke 20p low {pl:.2f}" + (" ✓ volume" if vol_ok else " — weak vol")
        return StrategySignal("BREAKOUT", symbol, "short", conf, reason, 1.0, 3.5)

    pct_to_high = (ph - curr["close"]) / ph * 100
    return _hold(f"Range-bound — {pct_to_high:.1f}% from breakout")


def funding_arb_strategy(symbol: str, candles: list[dict],
                          funding_rate: float = 0.0) -> StrategySignal:
    """Trend-following biased by funding rate reset logic."""
    closes = [c["close"] for c in candles]
    _hold  = lambda r: StrategySignal("FUNDING_ARB", symbol, "hold", 0.0, r, 1.5, 3.0)

    if len(closes) < 50:
        return _hold("Insufficient data")

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    price = closes[-1]
    fast, slow = ema20[-1], ema50[-1]

    fr_pct = funding_rate * 100
    # Positive funding → longs paying → slight short bias on extreme
    fr_bias = -1 if fr_pct > 0.05 else (1 if fr_pct < -0.05 else 0)

    if fast > slow and price > fast and fr_bias >= 0:
        conf   = 0.82 if fr_bias > 0 else 0.66
        reason = f"Uptrend EMA20>{fast:.0f}>EMA50{slow:.0f}, funding {fr_pct:.4f}%"
        return StrategySignal("FUNDING_ARB", symbol, "long",  conf, reason, 1.5, 3.0)

    if fast < slow and price < fast and fr_bias <= 0:
        conf   = 0.82 if fr_bias < 0 else 0.66
        reason = f"Downtrend EMA20<{fast:.0f}<EMA50{slow:.0f}, funding {fr_pct:.4f}%"
        return StrategySignal("FUNDING_ARB", symbol, "short", conf, reason, 1.5, 3.0)

    return _hold(f"No clear trend. Funding {fr_pct:.4f}%")


# ── Self-learning weight engine ───────────────────────────────────────────────

class StrategyWeightEngine:
    """Persists per-strategy stats and auto-adjusts weights after 3+ trades."""

    def __init__(self) -> None:
        self.stats: dict[str, StrategyStats] = {s: StrategyStats() for s in STRATEGIES}
        self._load()

    def _load(self) -> None:
        if not _WEIGHTS_FILE.exists():
            return
        try:
            raw = json.loads(_WEIGHTS_FILE.read_text())
            for name, d in raw.items():
                if name in self.stats:
                    s         = self.stats[name]
                    s.trades  = d.get("trades", 0)
                    s.wins    = d.get("wins",   0)
                    s.losses  = d.get("losses", 0)
                    s.weight  = d.get("weight", 1.0)
        except Exception as e:
            logger.warning(f"Weight load failed: {e}")

    def save(self) -> None:
        _WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            name: {"trades": s.trades, "wins": s.wins,
                   "losses": s.losses, "weight": s.weight}
            for name, s in self.stats.items()
        }
        _WEIGHTS_FILE.write_text(json.dumps(raw, indent=2))

    def record_result(self, strategy: str, won: bool) -> None:
        if strategy not in self.stats:
            return
        s = self.stats[strategy]
        s.trades += 1
        if won:
            s.wins += 1
        else:
            s.losses += 1
        s.update_weight()
        self.save()
        logger.info(
            f"[{strategy}] weight={s.weight:.2f}  "
            f"WR={s.win_rate:.0%}  trades={s.trades}"
        )

    def effective_confidence(self, strategy: str, raw_conf: float) -> float:
        w = self.stats.get(strategy, StrategyStats()).weight
        return min(1.0, raw_conf * w)

    def summary(self) -> dict:
        return {
            name: {
                "weight":   s.weight,
                "trades":   s.trades,
                "wins":     s.wins,
                "losses":   s.losses,
                "win_rate": round(s.win_rate * 100, 1),
            }
            for name, s in self.stats.items()
        }
