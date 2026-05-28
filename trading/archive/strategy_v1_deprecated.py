# DEPRECATED 2026-05-28 — superseded by trading/strategies.py (v2, weight-based)
# Kept for: core/scheduler.py (auto-scan), content/receiver.py (Telegram /scan command)
# Do not add new callers. Migrate to trading.strategies when those modules are updated.
"""
ClawBot — RSI + MACD Strategy Module
=====================================
Strategy: Buy when RSI is oversold AND MACD confirms bullish crossover.
          Sell when RSI is overbought OR MACD confirms bearish crossover.

Designed for: BTC, SOL, XRP, ETH (your core basket)
Timeframe:    4H candles (default) or Daily
Risk:         1-2% of portfolio per signal (configurable)

Backtest reference:
  - RSI-only: 773.65% return (2018-2022, PMC/NIH study)
  - RSI+MACD combined: 55-73% win rate
  - Bear market drawdown reduced from -65.75% to -41.40%
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("clawbot.trading.strategy")


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class RSIMACDConfig:
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    risk_per_trade_pct: float = 1.5
    max_open_positions: int = 4

    coins: list = field(default_factory=lambda: [
        "BTC_USDT", "SOL_USDT", "XRP_USDT", "ETH_USDT"
    ])


# ── Signal Model ──────────────────────────────────────────────────────────────

@dataclass
class Signal:
    coin: str
    action: str            # "BUY" | "SELL" | "HOLD"
    rsi: float
    macd: float
    macd_signal_val: float
    macd_histogram: float
    reason: str
    confidence: str        # "HIGH" | "MEDIUM" | "LOW"

    def to_telegram_message(self) -> str:
        emoji      = "🟢" if self.action == "BUY" else "🔴" if self.action == "SELL" else "⚪"
        conf_emoji = "💪" if self.confidence == "HIGH" else "👍" if self.confidence == "MEDIUM" else "🤔"
        return (
            f"{emoji} <b>ClawBot Signal — {self.coin}</b>\n"
            f"Action: <code>{self.action}</code>\n"
            f"Reason: {self.reason}\n\n"
            f"📊 <b>Indicators</b>\n"
            f"RSI:       <code>{self.rsi:.1f}</code>\n"
            f"MACD:      <code>{self.macd:.6f}</code>\n"
            f"Signal:    <code>{self.macd_signal_val:.6f}</code>\n"
            f"Histogram: <code>{self.macd_histogram:.6f}</code>\n\n"
            f"Confidence: {conf_emoji} <code>{self.confidence}</code>"
        )


# ── Indicators ────────────────────────────────────────────────────────────────

def calculate_rsi(closes: list, period: int = 14) -> float:
    """RSI via Wilder smoothing. Requires period+1 data points."""
    if len(closes) < period + 1:
        raise ValueError(f"Need {period + 1} closes for RSI, got {len(closes)}.")

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_ema(values: list, period: int) -> list:
    """EMA series."""
    if len(values) < period:
        raise ValueError(f"Need {period} values for EMA, got {len(values)}.")
    k   = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]
    for price in values[period:]:
        ema.append(price * k + ema[-1] * (1.0 - k))
    return ema


def calculate_macd(closes: list, fast: int = 12, slow: int = 26, signal_period: int = 9):
    """Returns (macd_line, signal_line, histogram) for the latest candle."""
    if len(closes) < slow + signal_period:
        raise ValueError(f"Need {slow + signal_period} closes for MACD, got {len(closes)}.")

    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)

    offset      = len(ema_fast) - len(ema_slow)
    macd_series = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    sig_series  = calculate_ema(macd_series, signal_period)

    macd_val = macd_series[-1]
    sig_val  = sig_series[-1]
    return macd_val, sig_val, macd_val - sig_val


def detect_macd_crossover(closes: list, fast: int = 12, slow: int = 26, signal_period: int = 9) -> str:
    """Returns 'bullish_cross' | 'bearish_cross' | 'none'."""
    if len(closes) < slow + signal_period + 2:
        return "none"
    m1, s1, _ = calculate_macd(closes,      fast, slow, signal_period)
    m0, s0, _ = calculate_macd(closes[:-1], fast, slow, signal_period)
    if m0 <= s0 and m1 > s1:
        return "bullish_cross"
    if m0 >= s0 and m1 < s1:
        return "bearish_cross"
    return "none"


# ── Strategy Engine ───────────────────────────────────────────────────────────

class RSIMACDStrategy:
    def __init__(self, config: Optional[RSIMACDConfig] = None):
        self.config = config or RSIMACDConfig()

    def evaluate(self, coin: str, closes: list) -> Signal:
        cfg = self.config
        try:
            rsi             = calculate_rsi(closes, cfg.rsi_period)
            macd, sig, hist = calculate_macd(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
            crossover       = detect_macd_crossover(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        except ValueError as e:
            logger.warning(f"[{coin}] Skipped — {e}")
            return Signal(coin=coin, action="HOLD", rsi=0, macd=0,
                          macd_signal_val=0, macd_histogram=0,
                          reason="Insufficient candle data.", confidence="LOW")

        if rsi < cfg.rsi_oversold and crossover == "bullish_cross":
            return Signal(coin=coin, action="BUY", rsi=rsi, macd=macd,
                          macd_signal_val=sig, macd_histogram=hist,
                          reason=f"RSI oversold ({rsi:.1f}) + fresh MACD bullish cross. Prime accumulation.",
                          confidence="HIGH")

        if rsi < cfg.rsi_oversold and hist > 0:
            return Signal(coin=coin, action="BUY", rsi=rsi, macd=macd,
                          macd_signal_val=sig, macd_histogram=hist,
                          reason=f"RSI oversold ({rsi:.1f}), MACD bullish momentum. Accumulation zone.",
                          confidence="MEDIUM")

        if rsi > cfg.rsi_overbought and crossover == "bearish_cross":
            return Signal(coin=coin, action="SELL", rsi=rsi, macd=macd,
                          macd_signal_val=sig, macd_histogram=hist,
                          reason=f"RSI overbought ({rsi:.1f}) + fresh MACD bearish cross. Take profits.",
                          confidence="HIGH")

        if rsi > cfg.rsi_overbought and hist < 0:
            return Signal(coin=coin, action="SELL", rsi=rsi, macd=macd,
                          macd_signal_val=sig, macd_histogram=hist,
                          reason=f"RSI overbought ({rsi:.1f}), MACD bearish. Trim position.",
                          confidence="MEDIUM")

        return Signal(coin=coin, action="HOLD", rsi=rsi, macd=macd,
                      macd_signal_val=sig, macd_histogram=hist,
                      reason=f"RSI neutral ({rsi:.1f}). No crossover. Watching.",
                      confidence="LOW")

    def scan_all(self, candle_data: dict) -> list:
        """Scan all coins. Returns only BUY/SELL signals."""
        actionable = []
        for coin in self.config.coins:
            if coin not in candle_data:
                logger.warning(f"No data for {coin}, skipping.")
                continue
            signal = self.evaluate(coin, candle_data[coin])
            logger.info(f"[{coin}] {signal.action} | RSI={signal.rsi:.1f} | "
                        f"Hist={signal.macd_histogram:.6f} | Conf={signal.confidence}")
            if signal.action != "HOLD":
                actionable.append(signal)
        return actionable


# ── Position Sizing ───────────────────────────────────────────────────────────

def calculate_position_size(portfolio_usd: float, coin_price: float, risk_pct: float = 1.5) -> dict:
    usd_amount    = portfolio_usd * (risk_pct / 100.0)
    coin_quantity = usd_amount / coin_price
    return {
        "usd_amount":    round(usd_amount, 2),
        "coin_quantity": round(coin_quantity, 8),
        "risk_pct":      risk_pct,
    }
