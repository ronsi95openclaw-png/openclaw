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
    """EMA 9/21 crossover with RSI momentum gate and ATR-based SL.

    Only fires on a fresh cross where the EMAs have separated by ≥0.05%
    (avoids whipsaw on flat markets) and RSI confirms the direction.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.2, tp=3.0: StrategySignal("EMA_CROSS", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 25:
        return _hold("Insufficient data")

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    curr9, prev9   = ema9[-1],  ema9[-2]
    curr21, prev21 = ema21[-1], ema21[-2]

    price = closes[-1]
    atr   = _atr(candles[-15:], 14)
    sl    = max(1.2, (atr / price * 100) * 2.0) if price > 0 else 1.2
    tp    = sl * 2.5   # 2.5:1 R:R

    gap_pct = abs(curr9 - curr21) / curr21 * 100
    if gap_pct < 0.05:
        return _hold(f"EMA gap too flat ({gap_pct:.3f}%) — skipping whipsaw", sl, tp)

    rsi = _rsi(closes, 14)

    if prev9 <= prev21 and curr9 > curr21 and rsi > 45:
        conf   = 0.82 if rsi > 55 else 0.72
        reason = f"EMA9 crossed above EMA21 (gap {gap_pct:.2f}%), RSI {rsi:.1f}"
        return StrategySignal("EMA_CROSS", symbol, "long",  conf, reason, sl, tp)

    if prev9 >= prev21 and curr9 < curr21 and rsi < 55:
        conf   = 0.82 if rsi < 45 else 0.72
        reason = f"EMA9 crossed below EMA21 (gap {gap_pct:.2f}%), RSI {rsi:.1f}"
        return StrategySignal("EMA_CROSS", symbol, "short", conf, reason, sl, tp)

    return _hold(f"No valid cross — gap {gap_pct:.2f}%, RSI {rsi:.1f}", sl, tp)


def rsi_mean_revert_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """RSI mean-reversion with trend guard, tighter thresholds, and 2.5:1 R:R.

    Only fires in ranging conditions (EMA20/EMA50 spread < 0.25%).
    Thresholds raised to RSI < 25 / > 75 — rarer but higher quality signals.
    SL floor raised to 1.2% (2× ATR min) to survive normal noise.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.5, tp=3.75: StrategySignal("RSI_MEAN_REVERT", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 25:
        return _hold("Insufficient data")

    rsi   = _rsi(closes, 14)
    price = closes[-1]
    atr   = _atr(candles[-15:], 14)
    sl    = max(1.2, (atr / price * 100) * 2.0) if price > 0 else 1.5
    tp    = sl * 2.5   # 2.5:1 R:R

    # Trend guard: don't mean-revert into a strong trend or a mean-reverting regime
    ema20  = _ema(closes, 20)
    ema50  = _ema(closes, 50) if len(closes) >= 50 else _ema(closes, len(closes) // 2)
    spread = abs(ema20[-1] - ema50[-1]) / ema50[-1] * 100
    if spread > 0.25:
        return _hold(f"Trending market (EMA spread {spread:.2f}%) — no mean-revert", sl, tp)

    # Additional slope guard: if EMA20 is moving strongly in one direction,
    # fading it is fighting momentum. Require EMA20 slope (last 3 bars) < 0.05%.
    ema20_slope = abs(ema20[-1] - ema20[-4]) / ema20[-4] * 100 if len(ema20) >= 4 else 0.0
    if ema20_slope > 0.05:
        return _hold(f"EMA20 slope too steep ({ema20_slope:.3f}%) — momentum risk", sl, tp)

    if rsi < 25:
        conf = 0.90 if rsi < 20 else 0.75
        return StrategySignal("RSI_MEAN_REVERT", symbol, "long", conf,
                              f"RSI deep oversold {rsi:.1f}, ranging (spread {spread:.2f}%)", sl, tp)
    if rsi > 75:
        conf = 0.90 if rsi > 80 else 0.75
        return StrategySignal("RSI_MEAN_REVERT", symbol, "short", conf,
                              f"RSI deep overbought {rsi:.1f}, ranging (spread {spread:.2f}%)", sl, tp)

    return _hold(f"RSI neutral {rsi:.1f} (spread {spread:.2f}%)", sl, tp)


def breakout_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """20-period high/low breakout — ATR-based SL, body confirmation, strict volume.

    Improvements over v1:
    - ATR-based SL (2× ATR, min 1.5%) replaces the fixed 1% that was too tight.
    - TP = 3× SL giving a 3:1 R:R.
    - Close must exceed the breakout level by ≥0.10% (filters wick fakeouts).
    - Volume must be ≥1.5× the 20-period average for full confidence.
    - RSI gate: longs require RSI < 70, shorts require RSI > 30 (avoids chasing).
    """
    _hold = lambda r, sl=1.5, tp=4.5: StrategySignal("BREAKOUT", symbol, "hold", 0.0, r, sl, tp)

    if len(candles) < 22:
        return _hold("Insufficient data")

    lookback  = candles[-21:-1]
    curr      = candles[-1]
    price     = curr["close"]
    atr       = _atr(candles[-15:], 14)

    sl  = max(1.5, (atr / price * 100) * 2.0) if price > 0 else 1.5
    tp  = sl * 3.0   # 3:1 R:R

    ph        = max(c["high"]   for c in lookback)
    pl        = min(c["low"]    for c in lookback)
    avg_vol   = sum(c["volume"] for c in lookback) / len(lookback)
    vol_ratio = curr["volume"] / avg_vol if avg_vol > 0 else 1.0
    rsi       = _rsi([c["close"] for c in candles], 14)

    # Require a meaningful close beyond the level (not a wick poke)
    margin_up  = (price - ph) / ph * 100  if ph > 0 else 0.0
    margin_dn  = (pl - price) / pl * 100  if pl > 0 else 0.0

    if price > ph and margin_up >= 0.10:
        if vol_ratio < 1.2 or rsi >= 70:
            return _hold(f"Long breakout weak: vol×{vol_ratio:.1f} RSI {rsi:.1f}")
        conf   = 0.88 if vol_ratio >= 1.5 else 0.72
        reason = (f"Broke 20p high {ph:.2f} by {margin_up:.2f}%, "
                  f"vol×{vol_ratio:.1f}, RSI {rsi:.1f}")
        return StrategySignal("BREAKOUT", symbol, "long",  conf, reason, sl, tp)

    if price < pl and margin_dn >= 0.10:
        if vol_ratio < 1.2 or rsi <= 30:
            return _hold(f"Short breakout weak: vol×{vol_ratio:.1f} RSI {rsi:.1f}")
        conf   = 0.88 if vol_ratio >= 1.5 else 0.72
        reason = (f"Broke 20p low {pl:.2f} by {margin_dn:.2f}%, "
                  f"vol×{vol_ratio:.1f}, RSI {rsi:.1f}")
        return StrategySignal("BREAKOUT", symbol, "short", conf, reason, sl, tp)

    pct_to_high = (ph - price) / ph * 100
    return _hold(f"Range-bound — {pct_to_high:.1f}% from breakout", sl, tp)


def funding_arb_strategy(symbol: str, candles: list[dict],
                          funding_rate: float = 0.0) -> StrategySignal:
    """Trend-following gated by a meaningful funding rate signal.

    Only fires when funding is skewed enough to indicate overleverage (fr_bias != 0),
    the EMA20/EMA50 spread confirms a real trend (≥0.20%), and RSI agrees with
    the direction. This avoids the neutral-funding false entries that caused
    repeated losses when used as a plain EMA trend-follow.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r: StrategySignal("FUNDING_ARB", symbol, "hold", 0.0, r, 1.5, 3.0)

    if len(closes) < 50:
        return _hold("Insufficient data")

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    price = closes[-1]
    fast, slow = ema20[-1], ema50[-1]

    fr_pct = funding_rate * 100
    # Positive funding → longs overloaded → short bias; negative → long bias
    fr_bias = -1 if fr_pct > 0.05 else (1 if fr_pct < -0.05 else 0)

    # Require a real funding signal — neutral market has no funding edge
    if fr_bias == 0:
        return _hold(f"Funding neutral ({fr_pct:.4f}%) — no edge")

    # Require minimum EMA spread to confirm genuine trend, not noise
    ema_gap_pct = abs(fast - slow) / slow * 100
    if ema_gap_pct < 0.20:
        return _hold(f"EMA gap too narrow ({ema_gap_pct:.2f}%) — wait for trend")

    # RSI momentum filter: longs need RSI > 50, shorts need RSI < 50
    rsi = _rsi(closes, 14)
    atr = _atr(candles[-15:], 14)
    sl  = max(1.0, (atr / price * 100) * 2.0) if price > 0 else 1.5
    tp  = sl * 2.0

    if fast > slow and price > fast and fr_bias > 0 and rsi > 50:
        conf   = 0.78 if rsi > 55 else 0.68
        reason = (f"Uptrend gap {ema_gap_pct:.2f}%, RSI {rsi:.1f}, "
                  f"funding {fr_pct:.4f}% (long-biased)")
        return StrategySignal("FUNDING_ARB", symbol, "long",  conf, reason, sl, tp)

    if fast < slow and price < fast and fr_bias < 0 and rsi < 50:
        conf   = 0.78 if rsi < 45 else 0.68
        reason = (f"Downtrend gap {ema_gap_pct:.2f}%, RSI {rsi:.1f}, "
                  f"funding {fr_pct:.4f}% (short-biased)")
        return StrategySignal("FUNDING_ARB", symbol, "short", conf, reason, sl, tp)

    return _hold(f"Conditions unmet — EMA gap {ema_gap_pct:.2f}%, RSI {rsi:.1f}, "
                 f"fr_bias {fr_bias}")


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
