"""Crypto.com multi-strategy engine — v2 full overhaul.

Five strategies, each targeting a distinct market condition:

  EMA_CROSS      — EMA 9/21 crossover with volume + RSI gate
  RSI_MEAN_REVERT — Fade RSI extremes in ranging markets
  BREAKOUT       — 20-period high/low breakout, ATR-sized SL
  BOLLINGER_BAND — BB touch + RSI in low-volatility ranges (NEW)
  TREND_FOLLOW   — Triple EMA alignment with momentum gate (replaces FUNDING_ARB)

Self-learning weight engine (0.2× → 2.0×) adjusts allocation after 3+ trades.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("openclaw.trading.strategies")

_WEIGHTS_FILE = Path(__file__).parent.parent / "data" / "strategy_weights.json"

STRATEGIES = ["EMA_CROSS", "RSI_MEAN_REVERT", "BREAKOUT", "BOLLINGER_BAND", "TREND_FOLLOW"]


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class StrategySignal:
    strategy:   str
    symbol:     str
    action:     str     # "long" | "short" | "hold"
    confidence: float   # 0.0–1.0
    reason:     str
    sl_pct:     float
    tp_pct:     float


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
        wr        = self.win_rate
        new_w     = 0.2 + (wr ** 1.5) * 1.8   # 0.2 at 0% WR → 2.0 at 100%
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


def _bollinger_bands(closes: list[float], period: int = 20,
                     num_std: float = 2.0) -> tuple[float, float, float]:
    """Returns (upper, middle, lower) Bollinger Bands."""
    if len(closes) < period:
        mid = closes[-1]
        return mid, mid, mid
    window = closes[-period:]
    mid    = sum(window) / period
    var    = sum((x - mid) ** 2 for x in window) / period
    std    = math.sqrt(var)
    return mid + num_std * std, mid, mid - num_std * std


def _macd(closes: list[float],
          fast: int = 12, slow: int = 26, signal: int = 9
          ) -> tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    # Align lengths
    min_len  = min(len(ema_fast), len(ema_slow))
    macd_arr = [ema_fast[-(min_len-i)] - ema_slow[-(min_len-i)] for i in range(min_len)]
    if len(macd_arr) < signal:
        return macd_arr[-1], macd_arr[-1], 0.0
    sig_arr  = _ema(macd_arr, signal)
    hist     = macd_arr[-1] - sig_arr[-1]
    return macd_arr[-1], sig_arr[-1], hist


# ── Strategy implementations ──────────────────────────────────────────────────

def ema_cross_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """EMA 9/21 crossover — ATR SL, RSI + volume gate, 2.5:1 R:R."""
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.2, tp=3.0: StrategySignal("EMA_CROSS", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 25:
        return _hold("Insufficient data")

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    curr9, prev9   = ema9[-1],  ema9[-2]
    curr21, prev21 = ema21[-1], ema21[-2]

    price    = closes[-1]
    atr      = _atr(candles[-20:], 14)
    sl       = max(1.0, (atr / price * 100) * 1.5) if price > 0 else 1.2
    tp       = sl * 2.5
    gap_pct  = abs(curr9 - curr21) / curr21 * 100
    rsi      = _rsi(closes, 14)
    avg_vol  = sum(c["volume"] for c in candles[-20:]) / 20
    vol_ok   = candles[-1]["volume"] >= avg_vol * 0.7   # not dead volume

    if gap_pct < 0.04:
        return _hold(f"EMA gap flat ({gap_pct:.3f}%)", sl, tp)

    # Require price to be on the right side of EMA9 — confirms momentum behind the cross
    if prev9 <= prev21 and curr9 > curr21 and rsi > 45 and vol_ok and price > curr9:
        conf   = 0.82 if rsi > 55 else 0.72
        reason = f"EMA9 crossed above EMA21 | gap {gap_pct:.2f}% | RSI {rsi:.1f}"
        return StrategySignal("EMA_CROSS", symbol, "long",  conf, reason, sl, tp)

    if prev9 >= prev21 and curr9 < curr21 and rsi < 55 and vol_ok and price < curr9:
        conf   = 0.82 if rsi < 45 else 0.72
        reason = f"EMA9 crossed below EMA21 | gap {gap_pct:.2f}% | RSI {rsi:.1f}"
        return StrategySignal("EMA_CROSS", symbol, "short", conf, reason, sl, tp)

    return _hold(f"No valid cross | gap {gap_pct:.2f}% | RSI {rsi:.1f}", sl, tp)


def rsi_mean_revert_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """RSI mean-reversion in ranging markets — 2.5:1 R:R.

    Fires at RSI <27 / >73 when EMA20/50 spread shows a range (< 0.30%).
    Slope guard removed — EMA spread alone is sufficient to identify ranges.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.3, tp=3.25: StrategySignal("RSI_MEAN_REVERT", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 25:
        return _hold("Insufficient data")

    rsi   = _rsi(closes, 14)
    price = closes[-1]
    atr   = _atr(candles[-20:], 14)
    sl    = max(1.0, (atr / price * 100) * 1.5) if price > 0 else 1.3
    tp    = sl * 2.5

    ema20  = _ema(closes, 20)
    ema50  = _ema(closes, 50) if len(closes) >= 50 else _ema(closes, max(10, len(closes)//2))
    spread = abs(ema20[-1] - ema50[-1]) / ema50[-1] * 100

    if spread > 0.50:
        return _hold(f"Trend active (spread {spread:.2f}%) — skip mean-revert", sl, tp)

    # Require current candle body in recovery direction — prevents entering waterfalls
    bullish_body = closes[-1] >= candles[-1]["open"]   # green candle = bounce starting
    bearish_body = closes[-1] <= candles[-1]["open"]   # red candle = reversal starting

    if rsi < 27 and bullish_body:
        conf = 0.88 if rsi < 22 else 0.74
        return StrategySignal("RSI_MEAN_REVERT", symbol, "long", conf,
                              f"RSI oversold {rsi:.1f} + bullish candle | spread {spread:.2f}%", sl, tp)
    if rsi > 73 and bearish_body:
        conf = 0.88 if rsi > 78 else 0.74
        return StrategySignal("RSI_MEAN_REVERT", symbol, "short", conf,
                              f"RSI overbought {rsi:.1f} + bearish candle | spread {spread:.2f}%", sl, tp)

    return _hold(f"RSI neutral {rsi:.1f} | spread {spread:.2f}%", sl, tp)


def breakout_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """20-period high/low breakout — ATR SL, 0.08% margin, 1.1× volume min, 2.5:1 R:R."""
    _hold = lambda r, sl=1.3, tp=3.25: StrategySignal("BREAKOUT", symbol, "hold", 0.0, r, sl, tp)

    if len(candles) < 22:
        return _hold("Insufficient data")

    lookback  = candles[-21:-1]
    curr      = candles[-1]
    price     = curr["close"]
    atr       = _atr(candles[-20:], 14)
    sl        = max(1.2, (atr / price * 100) * 1.5) if price > 0 else 1.3
    tp        = sl * 2.5

    ph        = max(c["high"]   for c in lookback)
    pl        = min(c["low"]    for c in lookback)
    avg_vol   = sum(c["volume"] for c in lookback) / len(lookback)
    vol_ratio = curr["volume"] / avg_vol if avg_vol > 0 else 1.0
    rsi       = _rsi([c["close"] for c in candles], 14)

    margin_up = (price - ph) / ph * 100 if ph > 0 else 0.0
    margin_dn = (pl - price) / pl * 100 if pl > 0 else 0.0

    if price > ph and margin_up >= 0.08 and vol_ratio >= 1.1 and rsi < 75:
        conf   = 0.88 if vol_ratio >= 1.4 else 0.72
        reason = f"Broke 20p high {ph:.4f} +{margin_up:.2f}% | vol×{vol_ratio:.1f} | RSI {rsi:.1f}"
        return StrategySignal("BREAKOUT", symbol, "long",  conf, reason, sl, tp)

    if price < pl and margin_dn >= 0.08 and vol_ratio >= 1.1 and rsi > 25:
        conf   = 0.88 if vol_ratio >= 1.4 else 0.72
        reason = f"Broke 20p low {pl:.4f} -{margin_dn:.2f}% | vol×{vol_ratio:.1f} | RSI {rsi:.1f}"
        return StrategySignal("BREAKOUT", symbol, "short", conf, reason, sl, tp)

    pct_away = (ph - price) / ph * 100
    return _hold(f"Range-bound — {pct_away:.2f}% from breakout | RSI {rsi:.1f}", sl, tp)


def bollinger_band_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """Bollinger Band Squeeze Breakout — enter on expansion, not at band-touch.

    Previous approach (enter at band-touch) failed because in trending conditions
    price keeps moving past the SL before the mean-reversion TP is reached.

    New approach (squeeze breakout):
    1. Detect a squeeze: bandwidth contracted to <1.2% over the last 5 candles.
    2. Wait for expansion: current bandwidth > previous squeeze minimum × 1.5.
    3. Enter in the direction of the breakout (not counter-trend).
    4. Confirm with RSI momentum (long: RSI>50, short: RSI<50).

    This trades WITH momentum when volatility expands from a tight squeeze —
    the opposite of a mean-reversion entry.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.2, tp=3.0: StrategySignal("BOLLINGER_BAND", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 25:
        return _hold("Insufficient data")

    price = closes[-1]
    atr   = _atr(candles[-20:], 14)
    sl    = max(1.0, (atr / price * 100) * 1.5) if price > 0 else 1.2
    tp    = sl * 2.5   # 2.5:1 R:R momentum trade

    upper, mid, lower = _bollinger_bands(closes, 20, 2.0)
    bandwidth_now     = (upper - lower) / mid * 100 if mid > 0 else 999.0

    # Find the minimum bandwidth over the previous 5 candles (= squeeze level)
    bw_history = []
    for i in range(-6, -1):
        if abs(i) <= len(closes):
            w = closes[i - 5 : i]
            if len(w) >= 5:
                u, m, lo = _bollinger_bands(w, min(20, len(w)), 2.0)
                bw_history.append((u - lo) / m * 100 if m > 0 else 999.0)

    if not bw_history:
        return _hold("Insufficient BB history", sl, tp)

    bw_min = min(bw_history)

    # Must have squeezed tight recently — crypto baseline is 3-8%, squeeze = tight relative to that
    if bw_min > 4.0:
        return _hold(f"No recent squeeze (min BW {bw_min:.2f}%)", sl, tp)

    # Current bandwidth must be expanding from the squeeze
    if bandwidth_now < bw_min * 1.5:
        return _hold(f"No expansion yet (now {bandwidth_now:.2f}% vs min {bw_min:.2f}%)", sl, tp)

    rsi    = _rsi(closes, 14)
    # %B: 0=lower band, 1=upper band
    pct_b  = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    # Breakout long: price closed above middle + upper half, RSI bullish
    if pct_b > 0.65 and rsi > 52:
        conf   = 0.80 if rsi > 58 else 0.72
        reason = (f"BB squeeze breakout UP | %B {pct_b:.2f} | BW {bandwidth_now:.2f}% "
                  f"(was {bw_min:.2f}%) | RSI {rsi:.1f}")
        return StrategySignal("BOLLINGER_BAND", symbol, "long",  conf, reason, sl, tp)

    # Breakout short: price closed below middle + lower half, RSI bearish
    if pct_b < 0.35 and rsi < 48:
        conf   = 0.80 if rsi < 42 else 0.72
        reason = (f"BB squeeze breakout DOWN | %B {pct_b:.2f} | BW {bandwidth_now:.2f}% "
                  f"(was {bw_min:.2f}%) | RSI {rsi:.1f}")
        return StrategySignal("BOLLINGER_BAND", symbol, "short", conf, reason, sl, tp)

    return _hold(f"Squeeze detected but no clear direction | %B {pct_b:.2f} | RSI {rsi:.1f}", sl, tp)


def trend_follow_strategy(symbol: str, candles: list[dict]) -> StrategySignal:
    """Triple EMA alignment trend-following with MACD + volume confirmation.

    Requires EMA9 > EMA21 > EMA50 (or inverted for shorts), MACD histogram
    showing momentum, and volume above average. Fires in clear trending conditions
    where the other strategies tend to stay flat.
    3:1 R:R — trend trades run further so we give them room.
    """
    closes = [c["close"] for c in candles]
    _hold  = lambda r, sl=1.5, tp=4.5: StrategySignal("TREND_FOLLOW", symbol, "hold", 0.0, r, sl, tp)

    if len(closes) < 52:
        return _hold("Insufficient data (need 52 for EMA50+MACD)")

    ema9  = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)
    price = closes[-1]
    atr   = _atr(candles[-20:], 14)
    sl    = max(1.3, (atr / price * 100) * 2.0) if price > 0 else 1.5
    tp    = sl * 3.0   # 3:1 for trend trades

    e9, e21, e50 = ema9[-1], ema21[-1], ema50[-1]
    rsi          = _rsi(closes, 14)
    _, _, hist   = _macd(closes, 12, 26, 9)

    avg_vol  = sum(c["volume"] for c in candles[-20:]) / 20
    vol_ok   = candles[-1]["volume"] >= avg_vol * 1.0   # at least average volume

    # Bullish: full triple alignment, price above all EMAs, MACD positive, RSI 45-72
    if e9 > e21 > e50 and price > e9 and hist > 0 and 45 < rsi < 72 and vol_ok:
        gap_pct = (e9 - e50) / e50 * 100
        if gap_pct < 0.10:
            return _hold(f"Trend too early — EMA gap only {gap_pct:.2f}%", sl, tp)
        conf   = 0.82 if rsi > 55 and hist > 0 else 0.70
        reason = (f"Triple EMA bull | gap {gap_pct:.2f}% | "
                  f"MACD hist {hist:.4f} | RSI {rsi:.1f}")
        return StrategySignal("TREND_FOLLOW", symbol, "long",  conf, reason, sl, tp)

    # Bearish: full triple alignment down, price below all EMAs, MACD negative, RSI 28-55
    if e9 < e21 < e50 and price < e9 and hist < 0 and 28 < rsi < 55 and vol_ok:
        gap_pct = (e50 - e9) / e50 * 100
        if gap_pct < 0.10:
            return _hold(f"Trend too early — EMA gap only {gap_pct:.2f}%", sl, tp)
        conf   = 0.82 if rsi < 45 and hist < 0 else 0.70
        reason = (f"Triple EMA bear | gap {gap_pct:.2f}% | "
                  f"MACD hist {hist:.4f} | RSI {rsi:.1f}")
        return StrategySignal("TREND_FOLLOW", symbol, "short", conf, reason, sl, tp)

    return _hold(f"No triple alignment | RSI {rsi:.1f} | MACD {hist:.4f}", sl, tp)


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
                    # Integrity check: wins+losses must equal trades.
                    # If not, the record was saved by an older code version that
                    # didn't track outcomes. Reset counters so weight engine has
                    # a clean baseline — weight is preserved.
                    if s.trades > 0 and (s.wins + s.losses) == 0:
                        logger.warning(
                            "Weight integrity: %s has trades=%d but wins=0 losses=0 "
                            "— resetting to clean slate (was weight=%.2f)",
                            name, s.trades, s.weight,
                        )
                        s.trades = 0
                        s.weight = 1.0   # rehabilitate — give it a fresh start
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
