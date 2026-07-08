"""
ClawBot — Hybrid Trading Strategy (Blofin Futures)
====================================================
Architecture:
  Layer 1 (Daily)  — EMA50 trend bias filter
                     LONG bias if price > EMA50
                     SHORT bias if price < EMA50
                     NEUTRAL if within 0.3% of EMA (chop zone — no trade)

  Layer 2 (4H)     — Swing high/low breakout trigger
                     + EMA12/26 stack confirmation
                     + RSI50 momentum filter
                     + Volume confirmation (above 20-period avg)
                     All 4 conditions required for a signal

Risk Management:
  - 1.5% equity risk per trade
  - ATR-based stop loss
  - TP1 at 1.5× ATR → closes 50%, locks breakeven
  - TP2 at 3.5× ATR → exits runner

Pairs: BTC-USDT-PERP, ETH-USDT-PERP, SOL-USDT-PERP, XRP-USDT-PERP

Paper trading:
  - PaperTrader class — in-memory, no API calls
  - Tracks equity, PnL, win rate per pair

Feed real candles via Blofin REST API (next build session).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("clawbot.trading.strategy_v2")

PAIRS = ["BTC-USDT-PERP", "ETH-USDT-PERP", "SOL-USDT-PERP", "XRP-USDT-PERP"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    pair: str
    action: str          # "LONG" | "SHORT" | "NEUTRAL"
    confidence: str      # "HIGH" | "LOW"
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    atr: float
    reason: str

    def to_telegram_message(self) -> str:
        if self.action == "NEUTRAL":
            return f"⚪ <b>{self.pair}</b> — Neutral (chop zone, no trade)"

        icon = "🟢" if self.action == "LONG" else "🔴"
        conf_icon = "🔥" if self.confidence == "HIGH" else "⚡"

        return (
            f"{icon} <b>{self.pair} {self.action}</b> {conf_icon} {self.confidence}\n"
            f"📍 Entry:  <code>${self.entry:,.4f}</code>\n"
            f"🛑 SL:     <code>${self.stop_loss:,.4f}</code>\n"
            f"🎯 TP1:    <code>${self.tp1:,.4f}</code>  (50% close)\n"
            f"🏁 TP2:    <code>${self.tp2:,.4f}</code>  (runner)\n"
            f"📐 ATR:    <code>${self.atr:,.4f}</code>\n"
            f"💬 {self.reason}"
        )


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _ema(closes: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(closes[:period]) / period]
    for price in closes[period:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def _atr(candles: list[Candle], period: int = 14) -> float:
    """Average True Range."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not trs:
        return 0.0
    # Wilder smoothing
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _swing_high(candles: list[Candle], lookback: int = 20) -> float:
    """Highest high over lookback candles (excluding last)."""
    window = candles[-(lookback + 1):-1]
    return max(c.high for c in window) if window else 0.0


def _swing_low(candles: list[Candle], lookback: int = 20) -> float:
    """Lowest low over lookback candles (excluding last)."""
    window = candles[-(lookback + 1):-1]
    return min(c.low for c in window) if window else float("inf")


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI using Wilder smoothing."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _avg_volume(candles: list[Candle], period: int = 20) -> float:
    """Average volume over last N candles (excluding current)."""
    window = candles[-(period + 1):-1]
    if not window:
        return 0.0
    return sum(c.volume for c in window) / len(window)


# ---------------------------------------------------------------------------
# Layer 1 — Daily EMA50 bias
# ---------------------------------------------------------------------------

def _daily_bias(daily_candles: list[Candle]) -> str:
    """
    Returns 'LONG', 'SHORT', or 'NEUTRAL'.
    NEUTRAL = price within 0.3% of EMA50 (chop zone).
    """
    if len(daily_candles) < 50:
        return "NEUTRAL"

    closes = [c.close for c in daily_candles]
    ema50_series = _ema(closes, 50)
    if not ema50_series:
        return "NEUTRAL"

    ema50 = ema50_series[-1]
    price = closes[-1]
    pct_diff = abs(price - ema50) / ema50

    if pct_diff <= 0.003:   # within 0.3% — chop zone
        return "NEUTRAL"
    return "LONG" if price > ema50 else "SHORT"


# ---------------------------------------------------------------------------
# Layer 2 — 4H breakout trigger
# ---------------------------------------------------------------------------

def _four_hour_signal(
    h4_candles: list[Candle],
    bias: str,
    atr: float,
) -> Optional[Signal]:
    """
    Returns Signal or None.
    All 4 conditions must align with daily bias:
      1. Breakout of swing high (LONG) or swing low (SHORT)
      2. EMA12 > EMA26 (LONG) or EMA12 < EMA26 (SHORT)
      3. RSI > 50 (LONG) or RSI < 50 (SHORT)
      4. Current volume > 20-period average
    """
    if len(h4_candles) < 30 or bias == "NEUTRAL" or atr == 0:
        return None

    closes = [c.close for c in h4_candles]
    current = h4_candles[-1]
    price = current.close

    # EMA stack
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    if not ema12 or not ema26:
        return None
    ema12_val, ema26_val = ema12[-1], ema26[-1]

    # RSI
    rsi = _rsi(closes, 14)

    # Volume
    avg_vol = _avg_volume(h4_candles, 20)
    vol_ok = current.volume > avg_vol if avg_vol > 0 else False

    reasons = []

    if bias == "LONG":
        swing_h = _swing_high(h4_candles, 20)
        breakout = current.close > swing_h
        ema_ok = ema12_val > ema26_val
        rsi_ok = rsi > 50

        conditions = [breakout, ema_ok, rsi_ok, vol_ok]
        if not all(conditions):
            return None

        reasons.append(f"Breakout above ${swing_h:,.4f}")
        reasons.append(f"EMA12 {ema12_val:,.2f} > EMA26 {ema26_val:,.2f}")
        reasons.append(f"RSI {rsi:.1f}")
        reasons.append(f"Vol {current.volume:,.0f} > avg {avg_vol:,.0f}")

        sl = price - atr
        tp1 = price + atr * 1.5
        tp2 = price + atr * 3.5

        return Signal(
            pair="",  # filled by caller
            action="LONG",
            confidence="HIGH",
            entry=price,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            atr=atr,
            reason=" | ".join(reasons),
        )

    else:  # SHORT
        swing_l = _swing_low(h4_candles, 20)
        breakout = current.close < swing_l
        ema_ok = ema12_val < ema26_val
        rsi_ok = rsi < 50

        conditions = [breakout, ema_ok, rsi_ok, vol_ok]
        if not all(conditions):
            return None

        reasons.append(f"Breakdown below ${swing_l:,.4f}")
        reasons.append(f"EMA12 {ema12_val:,.2f} < EMA26 {ema26_val:,.2f}")
        reasons.append(f"RSI {rsi:.1f}")
        reasons.append(f"Vol {current.volume:,.0f} > avg {avg_vol:,.0f}")

        sl = price + atr
        tp1 = price - atr * 1.5
        tp2 = price - atr * 3.5

        return Signal(
            pair="",
            action="SHORT",
            confidence="HIGH",
            entry=price,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            atr=atr,
            reason=" | ".join(reasons),
        )


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

def evaluate_pair(
    pair: str,
    daily_candles: list[Candle],
    h4_candles: list[Candle],
) -> Signal:
    """
    Full evaluation for one pair.

    Args:
        pair:          e.g. "BTC-USDT-PERP"
        daily_candles: 100+ Daily candles (OHLCV)
        h4_candles:    50+ 4H candles (OHLCV)

    Returns:
        Signal with action LONG/SHORT/NEUTRAL
    """
    bias = _daily_bias(daily_candles)

    if bias == "NEUTRAL":
        price = daily_candles[-1].close if daily_candles else 0.0
        return Signal(
            pair=pair,
            action="NEUTRAL",
            confidence="LOW",
            entry=price,
            stop_loss=0.0,
            tp1=0.0,
            tp2=0.0,
            atr=0.0,
            reason="Price within 0.3% of Daily EMA50 — chop zone",
        )

    atr = _atr(h4_candles, 14)
    signal = _four_hour_signal(h4_candles, bias, atr)

    if signal is None:
        price = h4_candles[-1].close if h4_candles else 0.0
        return Signal(
            pair=pair,
            action="NEUTRAL",
            confidence="LOW",
            entry=price,
            stop_loss=0.0,
            tp1=0.0,
            tp2=0.0,
            atr=atr,
            reason=f"Daily bias {bias} but 4H conditions not aligned",
        )

    signal.pair = pair
    return signal


def scan_all(candle_data: dict[str, dict]) -> list[Signal]:
    """
    Scan all pairs.

    Args:
        candle_data: {
            "BTC-USDT-PERP": {
                "daily": [Candle, ...],
                "h4":    [Candle, ...],
            },
            ...
        }

    Returns:
        List of Signal objects for all pairs.
    """
    signals = []
    for pair in PAIRS:
        if pair not in candle_data:
            logger.warning(f"No candle data for {pair}")
            continue
        data = candle_data[pair]
        sig = evaluate_pair(pair, data.get("daily", []), data.get("h4", []))
        signals.append(sig)
        logger.info(f"{pair}: {sig.action} ({sig.confidence}) — {sig.reason[:60]}")
    return signals


# ---------------------------------------------------------------------------
# Risk sizing
# ---------------------------------------------------------------------------

def calculate_position_size(
    equity_usd: float,
    entry: float,
    stop_loss: float,
    risk_pct: float = 1.5,
) -> dict:
    """
    Calculate position size based on equity risk %.

    Returns dict with qty (contracts), usd_risk, leverage_hint.
    """
    if entry <= 0 or abs(entry - stop_loss) == 0:
        return {"qty": 0, "usd_risk": 0, "leverage_hint": 1}

    risk_usd = equity_usd * (risk_pct / 100)
    distance = abs(entry - stop_loss)
    qty = risk_usd / distance           # units of base asset
    notional = qty * entry              # USD notional

    # Suggest leverage to keep margin reasonable (cap at 10×)
    leverage = min(10, max(1, round(notional / (equity_usd * 0.1))))

    return {
        "qty": round(qty, 6),
        "usd_risk": round(risk_usd, 2),
        "notional_usd": round(notional, 2),
        "leverage_hint": leverage,
    }


# ---------------------------------------------------------------------------
# Paper trader
# ---------------------------------------------------------------------------

@dataclass
class _PaperPosition:
    pair: str
    action: str
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    qty: float
    tp1_hit: bool = False


@dataclass
class PaperTrader:
    """
    In-memory paper trading engine.

    Usage:
        pt = PaperTrader(equity=10_000)
        signal = evaluate_pair("BTC-USDT-PERP", daily, h4)
        pt.open(signal, equity=pt.equity)
        pt.update_all({"BTC-USDT-PERP": current_price})
        print(pt.summary())
    """
    equity: float = 10_000.0
    risk_pct: float = 1.5
    _positions: list[_PaperPosition] = field(default_factory=list)
    _closed: list[dict] = field(default_factory=list)

    def open(self, signal: Signal) -> Optional[dict]:
        """Open a paper position from a Signal. Only HIGH confidence."""
        if signal.action == "NEUTRAL" or signal.confidence != "HIGH":
            return None
        # Close any existing position for this pair
        self.close_pair(signal.pair, signal.entry)

        sizing = calculate_position_size(
            self.equity, signal.entry, signal.stop_loss, self.risk_pct
        )
        pos = _PaperPosition(
            pair=signal.pair,
            action=signal.action,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            qty=sizing["qty"],
        )
        self._positions.append(pos)
        logger.info(f"[PAPER] Opened {signal.action} {signal.pair} @ {signal.entry} qty={sizing['qty']}")
        return {"status": "opened", "pair": signal.pair, "sizing": sizing}

    def update_all(self, prices: dict[str, float]) -> list[dict]:
        """
        Feed current prices. Handles SL/TP1/TP2 hits.
        Returns list of closed trade results.
        """
        results = []
        still_open = []

        for pos in self._positions:
            price = prices.get(pos.pair)
            if price is None:
                still_open.append(pos)
                continue

            # TP1 hit — close 50%, lock BE
            if not pos.tp1_hit:
                if (pos.action == "LONG" and price >= pos.tp1) or \
                   (pos.action == "SHORT" and price <= pos.tp1):
                    half_qty = pos.qty * 0.5
                    pnl = half_qty * (price - pos.entry) if pos.action == "LONG" else half_qty * (pos.entry - price)
                    self.equity += pnl
                    pos.qty -= half_qty
                    pos.stop_loss = pos.entry  # lock breakeven
                    pos.tp1_hit = True
                    result = {"pair": pos.pair, "event": "TP1", "pnl": round(pnl, 2), "price": price}
                    results.append(result)
                    self._closed.append(result)
                    still_open.append(pos)
                    logger.info(f"[PAPER] TP1 hit {pos.pair} @ {price} PnL={pnl:.2f}")
                    continue

            # SL hit
            if (pos.action == "LONG" and price <= pos.stop_loss) or \
               (pos.action == "SHORT" and price >= pos.stop_loss):
                pnl = pos.qty * (price - pos.entry) if pos.action == "LONG" else pos.qty * (pos.entry - price)
                self.equity += pnl
                result = {"pair": pos.pair, "event": "SL", "pnl": round(pnl, 2), "price": price}
                results.append(result)
                self._closed.append(result)
                logger.info(f"[PAPER] SL hit {pos.pair} @ {price} PnL={pnl:.2f}")
                continue

            # TP2 hit
            if (pos.action == "LONG" and price >= pos.tp2) or \
               (pos.action == "SHORT" and price <= pos.tp2):
                pnl = pos.qty * (price - pos.entry) if pos.action == "LONG" else pos.qty * (pos.entry - price)
                self.equity += pnl
                result = {"pair": pos.pair, "event": "TP2", "pnl": round(pnl, 2), "price": price}
                results.append(result)
                self._closed.append(result)
                logger.info(f"[PAPER] TP2 hit {pos.pair} @ {price} PnL={pnl:.2f}")
                continue

            still_open.append(pos)

        self._positions = still_open
        return results

    def close_pair(self, pair: str, price: float) -> None:
        """Force-close any open position for a pair at given price."""
        remaining = []
        for pos in self._positions:
            if pos.pair == pair:
                pnl = pos.qty * (price - pos.entry) * (1 if pos.action == "LONG" else -1)
                self.equity += pnl
                self._closed.append({"pair": pair, "event": "MANUAL_CLOSE", "pnl": round(pnl, 2), "price": price})
            else:
                remaining.append(pos)
        self._positions = remaining

    def summary(self) -> dict:
        """Return performance summary."""
        wins = [t for t in self._closed if t["pnl"] > 0]
        losses = [t for t in self._closed if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self._closed)
        win_rate = len(wins) / len(self._closed) * 100 if self._closed else 0.0
        return {
            "equity": round(self.equity, 2),
            "total_trades": len(self._closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "open_positions": len(self._positions),
        }

    def format_summary_message(self) -> str:
        """Telegram-ready summary string."""
        s = self.summary()
        return (
            f"📊 <b>Paper Trader Summary</b>\n\n"
            f"💰 Equity:      <code>${s['equity']:,.2f}</code>\n"
            f"📈 Total PnL:   <code>${s['total_pnl']:,.2f}</code>\n"
            f"🏆 Win Rate:    <code>{s['win_rate_pct']}%</code>  "
            f"({s['wins']}W / {s['losses']}L)\n"
            f"📋 Total Trades: {s['total_trades']}\n"
            f"🔓 Open:         {s['open_positions']}"
        )


# ---------------------------------------------------------------------------
# Telegram formatter
# ---------------------------------------------------------------------------

def format_signal_message(signals: list[Signal]) -> str:
    """Format all signals as a Telegram HTML message."""
    if not signals:
        return "📭 No signals generated."

    active = [s for s in signals if s.action != "NEUTRAL"]
    neutral = [s for s in signals if s.action == "NEUTRAL"]

    lines = ["<b>🎯 Strategy Scan Results</b>\n"]
    for s in active:
        lines.append(s.to_telegram_message())
        lines.append("")
    if neutral:
        lines.append("⚪ <b>Neutral (no trade):</b>")
        for s in neutral:
            lines.append(f"  • {s.pair} — {s.reason}")

    return "\n".join(lines)
