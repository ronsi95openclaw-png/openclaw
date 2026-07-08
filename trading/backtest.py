"""
Backtest harness for the RSI+MACD strategy, plus the multi-strategy
Binance-history backtesting engine.

Two engines live in this module:

1. Pure walk-forward kernel (simulate_trade, walk_forward, summarize,
   BacktestResult, Trade) — walks forward through historical closes one
   candle at a time, asks the strategy for a signal at each step, and
   simulates a fixed-horizon trade on every HIGH-confidence BUY/SELL
   (which is exactly what the live executor would fire on). No network —
   fully unit-testable.

   Run directly:
       python -m trading.backtest                    # default 4h, 300 candles, $96 start
       python -m trading.backtest 1d 200 100         # timeframe, candles, starting_usd

2. Strategy-comparison engine (run_backtest, format_backtest_message,
   load_results) — downloads 4 years of OHLCV data from the Binance public
   API (no auth required) and backtests multiple strategies across all 4
   coin pairs.

   Strategies tested:
     1. RSI+MACD      — existing ClawBot strategy (RSI oversold/overbought + MACD crossover)
     2. EMA Crossover — EMA12/26 golden/death cross (classic trend following)
     3. Bollinger+RSI — BB squeeze breakout + RSI confirmation
     4. EMA Hybrid    — Daily EMA50 bias + 4H breakout (new trading_strategy.py)

   Data source: Binance public REST API (https://api.binance.com)
     GET /api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000
     No API key needed. Fetches up to 1000 candles per request.
     4 years = ~1460 daily candles (2 requests per pair).

   Results saved to: data/backtest_results.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

from trading.strategy import RSIMACDConfig, RSIMACDStrategy, Signal

logger = logging.getLogger("clawbot.trading.backtest")

_DATA_DIR     = Path(__file__).parent.parent / "data"
_RESULTS_FILE = _DATA_DIR / "backtest_results.json"
_BINANCE_URL  = "https://api.binance.com/api/v3/klines"

PAIRS = {
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
    "SOL-USDT": "SOLUSDT",
    "XRP-USDT": "XRPUSDT",
}

STRATEGIES = ["RSI_MACD", "EMA_CROSS", "BOLLINGER_RSI", "EMA_HYBRID"]


# ── Pure simulation kernel ────────────────────────────────────────────────────

@dataclass
class Trade:
    coin: str
    direction: str        # "BUY" or "SELL"
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    risk_amount: float
    pnl: float            # USD, signed
    rsi: float


def simulate_trade(
    direction: str,
    entry_price: float,
    exit_price: float,
    risk_amount: float,
) -> float:
    """Return signed USD P&L for a fixed-notional directional trade. Pure."""
    if entry_price <= 0:
        return 0.0
    pct = (exit_price - entry_price) / entry_price
    if direction == "SELL":
        pct = -pct
    return round(pct * risk_amount, 4)


@dataclass
class BacktestResult:
    coin: str
    trades: List[Trade] = field(default_factory=list)
    starting_balance: float = 0.0
    final_balance: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n_trades * 100.0) if self.n_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return round(sum(t.pnl for t in self.trades), 4)

    @property
    def expectancy(self) -> float:
        return round(self.total_pnl / self.n_trades, 4) if self.n_trades else 0.0


_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def walk_forward(
    coin: str,
    closes: List[float],
    *,
    strategy=None,
    horizon: int = 6,
    risk_pct: float = 1.5,
    starting_balance: float = 96.0,
    min_confidence: str = "HIGH",
) -> BacktestResult:
    """Walk forward through closes; trade on every signal at min_confidence or above.

    `strategy` can be any object exposing `evaluate(coin, closes) -> Signal`.
    Optional `.warmup` (int) overrides the default MACD-based warmup.
    Defaults to RSIMACDStrategy() so existing callers keep working.

    One open position per coin at a time (live bot also serializes orders per
    coin via the executor). Position size is risk_pct of CURRENT balance, so
    losses compound (matches live behavior).
    """
    strategy = strategy or RSIMACDStrategy()
    warmup = getattr(strategy, "warmup", None)
    if warmup is None:
        cfg = getattr(strategy, "config", None)
        warmup = (cfg.macd_slow + cfg.macd_signal + 2) if cfg else 40
    min_rank = _CONFIDENCE_RANK.get(min_confidence, 3)

    result = BacktestResult(coin=coin, starting_balance=starting_balance, final_balance=starting_balance)
    balance = starting_balance
    in_position_until = -1   # exclusive: indices < this are inside an open trade

    for i in range(warmup, len(closes)):
        if i < in_position_until:
            continue
        signal: Signal = strategy.evaluate(coin, closes[: i + 1])
        if signal.action not in ("BUY", "SELL"):
            continue
        if _CONFIDENCE_RANK.get(signal.confidence, 0) < min_rank:
            continue

        exit_idx = min(i + horizon, len(closes) - 1)
        if exit_idx <= i:
            break

        entry_price = closes[i]
        exit_price = closes[exit_idx]
        risk_amount = balance * (risk_pct / 100.0)
        pnl = simulate_trade(signal.action, entry_price, exit_price, risk_amount)

        balance = round(balance + pnl, 4)
        result.trades.append(Trade(
            coin=coin, direction=signal.action,
            entry_idx=i, exit_idx=exit_idx,
            entry_price=entry_price, exit_price=exit_price,
            risk_amount=round(risk_amount, 4), pnl=pnl, rsi=signal.rsi,
        ))
        in_position_until = exit_idx + 1   # lock out re-entry until trade exits

    result.final_balance = round(balance, 4)
    return result


def summarize(results: List[BacktestResult], starting_balance: float) -> dict:
    """Aggregate per-coin BacktestResults into one summary dict. Pure."""
    total_trades = sum(r.n_trades for r in results)
    total_wins = sum(r.wins for r in results)
    total_pnl = round(sum(r.total_pnl for r in results), 4)
    return {
        "coins": len(results),
        "total_trades": total_trades,
        "total_wins": total_wins,
        "overall_win_rate": round((total_wins / total_trades * 100.0) if total_trades else 0.0, 2),
        "total_pnl_usd": total_pnl,
        "ending_balance_usd": round(starting_balance + total_pnl, 4),
        "return_pct": round((total_pnl / starting_balance * 100.0) if starting_balance else 0.0, 2),
        "expectancy_per_trade_usd": round(total_pnl / total_trades, 4) if total_trades else 0.0,
    }


# ── I/O shell ─────────────────────────────────────────────────────────────────

def _format_table(per_coin: List[BacktestResult], overall: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("  BACKTEST — RSI+MACD strategy (HIGH-confidence signals only)")
    lines.append("=" * 70)
    lines.append(f"  {'coin':<10}  {'trades':>6}  {'wins':>5}  {'win%':>6}  {'pnl_usd':>10}  {'expectancy':>11}")
    lines.append("  " + "-" * 66)
    for r in per_coin:
        lines.append(
            f"  {r.coin:<10}  {r.n_trades:>6}  {r.wins:>5}  {r.win_rate:>6.1f}  {r.total_pnl:>+10.2f}  {r.expectancy:>+11.4f}"
        )
    lines.append("  " + "-" * 66)
    lines.append(
        f"  {'OVERALL':<10}  {overall['total_trades']:>6}  {overall['total_wins']:>5}  "
        f"{overall['overall_win_rate']:>6.1f}  {overall['total_pnl_usd']:>+10.2f}  "
        f"{overall['expectancy_per_trade_usd']:>+11.4f}"
    )
    lines.append("")
    lines.append(f"  Starting balance:  ${overall['ending_balance_usd'] - overall['total_pnl_usd']:.2f}")
    lines.append(f"  Ending balance:    ${overall['ending_balance_usd']:.2f}")
    lines.append(f"  Total return:      {overall['return_pct']:+.2f}%")
    lines.append("=" * 70)
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    timeframe = argv[1] if len(argv) > 1 else "4h"
    count = int(argv[2]) if len(argv) > 2 else 300
    starting = float(argv[3]) if len(argv) > 3 else 96.0
    horizon = int(argv[4]) if len(argv) > 4 else 6
    risk_pct = float(argv[5]) if len(argv) > 5 else 1.5
    min_conf = argv[6] if len(argv) > 6 else "HIGH"

    from trading.exchange import fetch_all_closes

    strategy = RSIMACDStrategy()
    coins = strategy.config.coins

    print(f"Fetching {count} {timeframe} candles per coin from Crypto.com (this hits the public API)...")
    candle_data = fetch_all_closes(coins, timeframe=timeframe, count=count)
    if not candle_data:
        print("No candle data returned. Aborting.")
        return 1

    print(f"Walking forward through {len(candle_data)} coins, "
          f"horizon={horizon} candles, risk_pct={risk_pct}%, starting=${starting:.2f}, "
          f"min_confidence={min_conf}")
    per_coin = []
    balance = starting
    for coin in coins:
        if coin not in candle_data:
            continue
        result = walk_forward(
            coin, candle_data[coin],
            strategy=strategy, horizon=horizon, risk_pct=risk_pct,
            starting_balance=balance, min_confidence=min_conf,
        )
        per_coin.append(result)
        balance = result.final_balance

    overall = summarize(per_coin, starting)
    print(_format_table(per_coin, overall))
    return 0


# ── Strategy-comparison engine (Binance 4yr history) ─────────────────────────

@dataclass
class BacktestCandle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class StrategyTrade:
    entry_time: str
    exit_time: str
    action: str       # BUY / SELL
    entry: float
    exit: float
    pnl_pct: float
    pnl_usd: float    # based on $10k starting equity


@dataclass
class StrategyResult:
    strategy: str
    pair: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_trade_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    equity_final: float   # starting from $10,000
    period: str


# ---------------------------------------------------------------------------
# Data fetcher — Binance public API
# ---------------------------------------------------------------------------

def _fetch_binance_klines(symbol: str, interval: str = "1d", days: int = 1460) -> list[BacktestCandle]:
    """
    Fetch historical OHLCV from Binance public API.
    4 years = 1460 daily candles (2 requests of 1000 each).
    """
    candles = []
    end_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    limit    = 1000

    # Calculate how many requests we need
    requests_needed = (days // limit) + (1 if days % limit else 0)
    start_ms = end_ms - (days * 86400 * 1000)

    current_start = start_ms
    for _ in range(requests_needed):
        try:
            resp = requests.get(
                _BINANCE_URL,
                params={
                    "symbol":    symbol,
                    "interval":  interval,
                    "startTime": current_start,
                    "limit":     limit,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            for row in data:
                candles.append(BacktestCandle(
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                ))

            current_start = data[-1][0] + 1  # next candle after last
            if len(data) < limit:
                break

            time.sleep(0.2)  # be nice to Binance public API

        except Exception as exc:
            logger.error(f"Binance fetch error for {symbol}: {exc}")
            break

    logger.info(f"Fetched {len(candles)} candles for {symbol}")
    return candles


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _ema(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return [closes[-1]] * len(closes)
    k = 2 / (period + 1)
    result = [sum(closes[:period]) / period]
    for p in closes[period:]:
        result.append(p * k + result[-1] * (1 - k))
    # Pad beginning
    return [result[0]] * (len(closes) - len(result)) + result


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    rsi_vals = [50.0] * period
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return rsi_vals

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period

    def _rsi_val(ag, al):
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    rsi_vals.append(_rsi_val(avg_g, avg_l))
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        rsi_vals.append(_rsi_val(avg_g, avg_l))

    return rsi_vals


def _macd(closes: list[float], fast=12, slow=26, signal=9) -> tuple[list[float], list[float]]:
    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_line  = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema(macd_line, signal)
    return macd_line, signal_line


def _bollinger(closes: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    """Returns (upper, middle, lower) bands."""
    upper, middle, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(closes[i])
            middle.append(closes[i])
            lower.append(closes[i])
        else:
            window = closes[i - period + 1:i + 1]
            sma    = sum(window) / period
            std    = (sum((x - sma) ** 2 for x in window) / period) ** 0.5
            upper.append(sma + std_dev * std)
            middle.append(sma)
            lower.append(sma - std_dev * std)
    return upper, middle, lower


def _atr(candles: list[BacktestCandle], period: int = 14) -> list[float]:
    trs = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    atrs = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        atrs.append((atrs[-1] * (period - 1) + tr) / period)
    return [atrs[0]] * (period - 1) + atrs


# ---------------------------------------------------------------------------
# Strategy 1: RSI + MACD
# ---------------------------------------------------------------------------

def _backtest_rsi_macd(candles: list[BacktestCandle]) -> list[StrategyTrade]:
    closes = [c.close for c in candles]
    rsi_vals          = _rsi(closes, 14)
    macd_line, sig_ln = _macd(closes)

    trades: list[StrategyTrade] = []
    in_trade   = False
    entry_idx  = 0
    entry_price = 0.0

    for i in range(26, len(candles)):
        rsi  = rsi_vals[i]
        macd = macd_line[i]
        sig  = sig_ln[i]
        prev_macd = macd_line[i - 1]
        prev_sig  = sig_ln[i - 1]

        if not in_trade:
            # Enter LONG: RSI < 35 AND MACD bullish crossover
            if rsi < 35 and prev_macd < prev_sig and macd > sig:
                in_trade    = True
                entry_idx   = i
                entry_price = candles[i].close

            # Enter SHORT: RSI > 65 AND MACD bearish crossover
            elif rsi > 65 and prev_macd > prev_sig and macd < sig:
                in_trade    = True
                entry_idx   = i
                entry_price = candles[i].close * -1  # negative = short

        else:
            is_long = entry_price > 0
            price   = candles[i].close
            ep      = abs(entry_price)

            # Exit conditions
            exit_signal = False
            if is_long and (rsi > 65 or (prev_macd > prev_sig and macd < sig)):
                exit_signal = True
            elif not is_long and (rsi < 35 or (prev_macd < prev_sig and macd > sig)):
                exit_signal = True

            # Max hold 30 days
            if i - entry_idx >= 30:
                exit_signal = True

            if exit_signal:
                pnl_pct = (price - ep) / ep * 100 * (1 if is_long else -1)
                trades.append(StrategyTrade(
                    entry_time=datetime.fromtimestamp(candles[entry_idx].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    exit_time=datetime.fromtimestamp(candles[i].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    action="BUY" if is_long else "SELL",
                    entry=ep,
                    exit=price,
                    pnl_pct=round(pnl_pct, 2),
                    pnl_usd=round(10000 * pnl_pct / 100, 2),
                ))
                in_trade = False

    return trades


# ---------------------------------------------------------------------------
# Strategy 2: EMA Crossover (12/26 golden cross)
# ---------------------------------------------------------------------------

def _backtest_ema_cross(candles: list[BacktestCandle]) -> list[StrategyTrade]:
    closes = [c.close for c in candles]
    ema12  = _ema(closes, 12)
    ema26  = _ema(closes, 26)

    trades: list[StrategyTrade] = []
    in_trade    = False
    entry_idx   = 0
    entry_price = 0.0
    is_long     = True

    for i in range(27, len(candles)):
        prev_cross = ema12[i - 1] - ema26[i - 1]
        curr_cross = ema12[i] - ema26[i]

        if not in_trade:
            if prev_cross < 0 and curr_cross > 0:   # golden cross → LONG
                in_trade    = True
                entry_idx   = i
                entry_price = candles[i].close
                is_long     = True
            elif prev_cross > 0 and curr_cross < 0:  # death cross → SHORT
                in_trade    = True
                entry_idx   = i
                entry_price = candles[i].close
                is_long     = False
        else:
            price       = candles[i].close
            exit_signal = False

            if is_long and prev_cross > 0 and curr_cross < 0:
                exit_signal = True
            elif not is_long and prev_cross < 0 and curr_cross > 0:
                exit_signal = True
            if i - entry_idx >= 60:
                exit_signal = True

            if exit_signal:
                pnl_pct = (price - entry_price) / entry_price * 100 * (1 if is_long else -1)
                trades.append(StrategyTrade(
                    entry_time=datetime.fromtimestamp(candles[entry_idx].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    exit_time=datetime.fromtimestamp(candles[i].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    action="BUY" if is_long else "SELL",
                    entry=entry_price,
                    exit=price,
                    pnl_pct=round(pnl_pct, 2),
                    pnl_usd=round(10000 * pnl_pct / 100, 2),
                ))
                in_trade = False

    return trades


# ---------------------------------------------------------------------------
# Strategy 3: Bollinger Bands + RSI
# ---------------------------------------------------------------------------

def _backtest_bollinger_rsi(candles: list[BacktestCandle]) -> list[StrategyTrade]:
    closes = [c.close for c in candles]
    upper, middle, lower = _bollinger(closes, 20, 2.0)
    rsi_vals = _rsi(closes, 14)

    trades: list[StrategyTrade] = []
    in_trade    = False
    entry_idx   = 0
    entry_price = 0.0
    is_long     = True

    for i in range(21, len(candles)):
        price = candles[i].close
        rsi   = rsi_vals[i]

        if not in_trade:
            # LONG: price touches lower band + RSI < 40 (oversold bounce)
            if price <= lower[i] and rsi < 40:
                in_trade    = True
                entry_idx   = i
                entry_price = price
                is_long     = True
            # SHORT: price touches upper band + RSI > 60 (overbought rejection)
            elif price >= upper[i] and rsi > 60:
                in_trade    = True
                entry_idx   = i
                entry_price = price
                is_long     = False
        else:
            exit_signal = False
            if is_long and price >= middle[i]:   # exit at middle band
                exit_signal = True
            elif not is_long and price <= middle[i]:
                exit_signal = True
            if i - entry_idx >= 20:
                exit_signal = True

            if exit_signal:
                pnl_pct = (price - entry_price) / entry_price * 100 * (1 if is_long else -1)
                trades.append(StrategyTrade(
                    entry_time=datetime.fromtimestamp(candles[entry_idx].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    exit_time=datetime.fromtimestamp(candles[i].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    action="BUY" if is_long else "SELL",
                    entry=entry_price,
                    exit=price,
                    pnl_pct=round(pnl_pct, 2),
                    pnl_usd=round(10000 * pnl_pct / 100, 2),
                ))
                in_trade = False

    return trades


# ---------------------------------------------------------------------------
# Strategy 4: EMA Hybrid (Daily EMA50 bias + 4H-style breakout on daily)
# ---------------------------------------------------------------------------

def _backtest_ema_hybrid(candles: list[BacktestCandle]) -> list[StrategyTrade]:
    closes = [c.close for c in candles]
    ema50  = _ema(closes, 50)
    ema12  = _ema(closes, 12)
    ema26  = _ema(closes, 26)
    rsi_vals = _rsi(closes, 14)
    atr_vals = _atr(candles, 14)

    trades: list[StrategyTrade] = []
    in_trade    = False
    entry_idx   = 0
    entry_price = 0.0
    stop_loss   = 0.0
    is_long     = True

    for i in range(51, len(candles)):
        price = candles[i].close
        rsi   = rsi_vals[i]
        atr   = atr_vals[i]

        # Daily EMA50 bias
        if abs(price - ema50[i]) / ema50[i] <= 0.003:
            continue   # chop zone — skip

        bias = "LONG" if price > ema50[i] else "SHORT"

        if not in_trade:
            ema_ok = ema12[i] > ema26[i] if bias == "LONG" else ema12[i] < ema26[i]
            rsi_ok = rsi > 50 if bias == "LONG" else rsi < 50

            # Swing high/low breakout (20-period)
            window = candles[max(0, i - 20):i]
            if bias == "LONG":
                swing = max(c.high for c in window)
                breakout = candles[i].high > swing
            else:
                swing = min(c.low for c in window)
                breakout = candles[i].low < swing

            if ema_ok and rsi_ok and breakout:
                in_trade    = True
                entry_idx   = i
                entry_price = price
                is_long     = bias == "LONG"
                stop_loss   = price - atr if is_long else price + atr

        else:
            exit_signal = False
            # SL hit
            if is_long and price <= stop_loss:
                exit_signal = True
            elif not is_long and price >= stop_loss:
                exit_signal = True

            # TP at 3× ATR
            tp = entry_price + atr_vals[entry_idx] * 3 if is_long else entry_price - atr_vals[entry_idx] * 3
            if is_long and price >= tp:
                exit_signal = True
            elif not is_long and price <= tp:
                exit_signal = True

            # Bias flip
            if (is_long and price < ema50[i]) or (not is_long and price > ema50[i]):
                exit_signal = True

            # Max hold 45 days
            if i - entry_idx >= 45:
                exit_signal = True

            if exit_signal:
                pnl_pct = (price - entry_price) / entry_price * 100 * (1 if is_long else -1)
                trades.append(StrategyTrade(
                    entry_time=datetime.fromtimestamp(candles[entry_idx].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    exit_time=datetime.fromtimestamp(candles[i].timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    action="BUY" if is_long else "SELL",
                    entry=entry_price,
                    exit=price,
                    pnl_pct=round(pnl_pct, 2),
                    pnl_usd=round(10000 * pnl_pct / 100, 2),
                ))
                in_trade = False

    return trades


# ---------------------------------------------------------------------------
# Metrics calculator
# ---------------------------------------------------------------------------

def _calc_metrics(trades: list[StrategyTrade], strategy: str, pair: str) -> StrategyResult:
    if not trades:
        return StrategyResult(
            strategy=strategy, pair=pair, total_trades=0, wins=0, losses=0,
            win_rate=0.0, total_return_pct=0.0, max_drawdown_pct=0.0,
            sharpe_ratio=0.0, avg_trade_pct=0.0, best_trade_pct=0.0,
            worst_trade_pct=0.0, equity_final=10000.0, period="4Y",
        )

    wins   = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    equity = 10000.0
    equity_curve = [equity]
    peak = equity

    for t in trades:
        equity *= (1 + t.pnl_pct / 100)
        equity_curve.append(equity)
        peak = max(peak, equity)

    total_return = (equity - 10000) / 10000 * 100
    max_dd = 0.0
    peak_eq = equity_curve[0]
    for eq in equity_curve:
        peak_eq = max(peak_eq, eq)
        dd = (peak_eq - eq) / peak_eq * 100
        max_dd = max(max_dd, dd)

    pnl_pcts = [t.pnl_pct for t in trades]
    avg_pnl = sum(pnl_pcts) / len(pnl_pcts)

    # Simplified Sharpe (daily returns / std)
    import statistics
    std = statistics.stdev(pnl_pcts) if len(pnl_pcts) > 1 else 1
    sharpe = avg_pnl / std if std > 0 else 0

    return StrategyResult(
        strategy=strategy,
        pair=pair,
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(trades) * 100, 1),
        total_return_pct=round(total_return, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 3),
        avg_trade_pct=round(avg_pnl, 2),
        best_trade_pct=round(max(pnl_pcts), 2),
        worst_trade_pct=round(min(pnl_pcts), 2),
        equity_final=round(equity, 2),
        period="4Y",
    )


# ---------------------------------------------------------------------------
# Full backtest runner
# ---------------------------------------------------------------------------

_STRATEGY_FNS = {
    "RSI_MACD":      _backtest_rsi_macd,
    "EMA_CROSS":     _backtest_ema_cross,
    "BOLLINGER_RSI": _backtest_bollinger_rsi,
    "EMA_HYBRID":    _backtest_ema_hybrid,
}


def run_backtest(pairs: Optional[list[str]] = None, days: int = 1460) -> dict:
    """
    Run all strategies on all pairs. Saves results to data/backtest_results.json.

    Args:
        pairs: list of pair keys from PAIRS dict (default: all 4)
        days:  number of days of history (default: 1460 = 4 years)

    Returns:
        results dict with rankings and per-pair/per-strategy breakdown
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    pairs = pairs or list(PAIRS.keys())

    all_results: list[StrategyResult] = []
    candle_cache: dict[str, list[BacktestCandle]] = {}

    for pair in pairs:
        symbol = PAIRS.get(pair)
        if not symbol:
            logger.warning(f"Unknown pair: {pair}")
            continue

        logger.info(f"Fetching {days} days of data for {pair}...")
        candles = _fetch_binance_klines(symbol, "1d", days)
        candle_cache[pair] = candles

        if len(candles) < 100:
            logger.error(f"Not enough data for {pair}: {len(candles)} candles")
            continue

        for strategy_name, fn in _STRATEGY_FNS.items():
            logger.info(f"  Backtesting {strategy_name} on {pair}...")
            try:
                trades  = fn(candles)
                metrics = _calc_metrics(trades, strategy_name, pair)
                all_results.append(metrics)
                logger.info(
                    f"    {strategy_name}/{pair}: {metrics.total_trades} trades, "
                    f"WR={metrics.win_rate}%, Return={metrics.total_return_pct}%"
                )
            except Exception as exc:
                logger.error(f"  Backtest failed {strategy_name}/{pair}: {exc}")

    # Rankings
    sorted_overall = sorted(all_results, key=lambda r: r.total_return_pct, reverse=True)

    # Best strategy per pair
    best_per_pair: dict[str, dict] = {}
    for pair in pairs:
        pair_results = [r for r in all_results if r.pair == pair]
        if pair_results:
            best = max(pair_results, key=lambda r: r.total_return_pct)
            best_per_pair[pair] = asdict(best)

    # Best pair per strategy
    best_per_strategy: dict[str, dict] = {}
    for strategy in STRATEGIES:
        strat_results = [r for r in all_results if r.strategy == strategy]
        if strat_results:
            best = max(strat_results, key=lambda r: r.total_return_pct)
            best_per_strategy[strategy] = asdict(best)

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": days,
        "overall_ranking": [asdict(r) for r in sorted_overall],
        "best_per_pair": best_per_pair,
        "best_per_strategy": best_per_strategy,
        "all_results": [asdict(r) for r in all_results],
    }

    _RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    logger.info(f"Backtest results saved to {_RESULTS_FILE}")
    return results


# ---------------------------------------------------------------------------
# Telegram formatter
# ---------------------------------------------------------------------------

def format_backtest_message(results: dict) -> str:
    """Format backtest results as Telegram HTML message."""
    if not results.get("overall_ranking"):
        return "❌ No backtest results available. Run /backtest first."

    top5 = results["overall_ranking"][:5]
    best_pair = results.get("best_per_pair", {})
    generated = results.get("generated_at", "")[:10]
    days = results.get("period_days", 1460)

    lines = [
        f"📊 <b>Backtest Results — {days // 365}Y History</b>",
        f"<i>Generated: {generated}</i>\n",
        "<b>🏆 Top 5 Strategy/Pair Combos (by Return):</b>",
    ]

    for i, r in enumerate(top5, 1):
        icon = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        lines.append(
            f"{icon} <b>{r['strategy']}</b> / {r['pair']}\n"
            f"   Return: <code>{r['total_return_pct']:+.1f}%</code> | "
            f"WR: <code>{r['win_rate']}%</code> | "
            f"DD: <code>{r['max_drawdown_pct']:.1f}%</code> | "
            f"Trades: {r['total_trades']}"
        )

    lines.append("\n<b>🥇 Best Strategy Per Coin:</b>")
    for pair, r in best_pair.items():
        lines.append(
            f"  {pair}: <b>{r['strategy']}</b> "
            f"→ <code>{r['total_return_pct']:+.1f}%</code> / {r['win_rate']}% WR"
        )

    lines.append(f"\n<i>Full data: data/backtest_results.json</i>")
    return "\n".join(lines)


def load_results() -> dict:
    """Load saved backtest results from disk."""
    if not _RESULTS_FILE.exists():
        return {}
    try:
        return json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
