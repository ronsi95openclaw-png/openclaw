"""
Backtest harness for the RSI+MACD strategy.

Walks forward through historical closes one candle at a time, asks the strategy
for a signal at each step, and simulates a fixed-horizon trade on every HIGH-
confidence BUY/SELL (which is exactly what the live executor would fire on).

Run directly:
    python -m trading.backtest                    # default 4h, 300 candles, $96 start
    python -m trading.backtest 1d 200 100         # timeframe, candles, starting_usd

The pure functions (simulate_trade, walk_forward, summarize) take only data —
no network — so they are fully unit-testable. fetch + main are the I/O shell.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import List, Optional

from trading.strategy import RSIMACDConfig, RSIMACDStrategy, Signal


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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
