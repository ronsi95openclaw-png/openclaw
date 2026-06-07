"""
Strategy comparison harness.

Runs all 5 strategies (4 candidates + RSI+MACD baseline) against the cached
historical candle data for BTC, ETH, SOL, XRP. Includes a 4-quarter regime
test on BTC to surface single-period winners (which is what we got burned by
on the 50-day backtest).

Reads `data/backtest/{symbol}_{timeframe}_1y.json` files produced by
`infra/fetch_historical_candles.py`. Default timeframe is 1d because the
public endpoint's no-pagination cap means 4h tops out at ~50 days — see
DECISIONS.md.

Writes a full JSON snapshot to `data/backtest/comparison_<ts>.json` and prints
the decision matrix.

Run:
    python -m infra.run_strategy_comparison              # 1d, default
    python -m infra.run_strategy_comparison 4h           # 4h (49-day span)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Make Unicode safe on Windows cp1252 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from trading.backtest import walk_forward, summarize, BacktestResult
from trading.strategy import RSIMACDStrategy
from trading.strategies.liquidity_sweep import LiquiditySweepStrategy
from trading.strategies.trend_continuation import TrendContinuationStrategy
from trading.strategies.breakout_expansion import BreakoutExpansionStrategy
from trading.strategies.ema_momentum import EmaMomentumStrategy


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "backtest"

STARTING_BALANCE = 96.00   # backtest narrative balance (not real money)
RISK_PCT = 1.5             # PERCENT, matches walk_forward's default semantics
HORIZON = 6                # candles to hold a position (6 candles = 6 days on 1d)
MIN_CONFIDENCE = "HIGH"    # mirror live executor

SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]
REGIME_TEST_SYMBOL = "BTC_USDT"
REGIME_SEGMENTS = 4

STRATEGY_FACTORIES = {
    "LiquiditySweep":    lambda: LiquiditySweepStrategy(),
    "TrendContinuation": lambda: TrendContinuationStrategy(),
    "BreakoutExpansion": lambda: BreakoutExpansionStrategy(),
    "EmaMomentum":       lambda: EmaMomentumStrategy(),
    "RSI_MACD_Baseline": lambda: RSIMACDStrategy(),
}


def load_closes(symbol: str, timeframe: str) -> list[float] | None:
    path = DATA_DIR / f"{symbol}_{timeframe}_1y.json"
    if not path.exists():
        print(f"  [skip] no data file: {path.name}")
        return None
    raw = json.loads(path.read_text())
    return [float(c["c"]) for c in raw]


def run_one(strategy_name: str, factory, symbol: str, closes: list[float], starting: float) -> dict:
    """Run one strategy on one symbol; return a flat dict for the report."""
    try:
        strategy = factory()
        result: BacktestResult = walk_forward(
            symbol, closes,
            strategy=strategy,
            horizon=HORIZON,
            risk_pct=RISK_PCT,
            starting_balance=starting,
            min_confidence=MIN_CONFIDENCE,
        )
        return_pct = ((result.final_balance - result.starting_balance) / result.starting_balance * 100.0) if result.starting_balance else 0.0
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "trades": result.n_trades,
            "wins": result.wins,
            "win_rate_pct": round(result.win_rate, 2),
            "pnl_usd": result.total_pnl,
            "final_balance": result.final_balance,
            "return_pct": round(return_pct, 3),
            "expectancy_usd": result.expectancy,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "error": f"{type(e).__name__}: {e}",
        }


def split_segments(closes: list[float], n_segments: int) -> list[list[float]]:
    seg_len = len(closes) // n_segments
    segs = []
    for i in range(n_segments):
        start = i * seg_len
        end = start + seg_len if i < n_segments - 1 else len(closes)
        segs.append(closes[start:end])
    return segs


def print_per_symbol_block(symbol: str, rows: list[dict]) -> None:
    print(f"\n--- {symbol} ---")
    print(f"  {'strategy':<22} {'trades':>6} {'win%':>6} {'pnl_usd':>9} {'return%':>8}")
    for r in rows:
        if "error" in r:
            print(f"  {r['strategy']:<22} ERROR: {r['error'][:60]}")
            continue
        print(
            f"  {r['strategy']:<22} {r['trades']:>6} "
            f"{r['win_rate_pct']:>6.1f} {r['pnl_usd']:>+9.3f} {r['return_pct']:>+8.2f}"
        )


def print_regime_block(regime_results: dict) -> None:
    print(f"\n--- REGIME TEST ({REGIME_TEST_SYMBOL}, {REGIME_SEGMENTS} segments) ---")
    header = "  " + "strategy".ljust(22) + "  " + "  ".join(f"  Q{i+1}   " for i in range(REGIME_SEGMENTS)) + "  +Q"
    print(header)
    for name, segs in regime_results.items():
        cells = []
        positive = 0
        for i in range(REGIME_SEGMENTS):
            v = segs.get(f"Q{i+1}", 0.0)
            if v > 0:
                positive += 1
            cells.append(f"{v:>+6.2f}%")
        print(f"  {name:<22}  " + "  ".join(cells) + f"  {positive}/{REGIME_SEGMENTS}")


def print_decision_matrix(rows: list[dict]) -> None:
    print(f"\n{'='*78}")
    print("DECISION MATRIX — sorted by regime resilience (+Quarters), then total PnL")
    print(f"{'='*78}")
    print(f"  {'strategy':<22} {'+Q':>4} {'trades':>7} {'win%':>6} {'totalPnL':>10} {'avgRet%':>9}")
    print("  " + "-" * 70)
    for r in rows:
        print(
            f"  {r['strategy']:<22} {r['positive_quarters']:>2}/{REGIME_SEGMENTS:<1} "
            f"{r['total_trades']:>7} "
            f"{r['avg_win_pct']:>6.1f} "
            f"${r['total_pnl']:>+8.3f} "
            f"{r['avg_return_pct']:>+8.2f}"
        )

    print()
    print("  Reading guide:")
    print("    +Q  : positive-return quarters out of " + str(REGIME_SEGMENTS) + " (regime resilience — HIGHER IS BETTER)")
    print("    Magnitude WITHOUT resilience = lucky regime; do NOT wire.")
    print("    A strategy with 4/4 quarters and $+0.50 beats one with 1/4 and $+2.00.")


def main() -> int:
    timeframe = sys.argv[1] if len(sys.argv) > 1 else "1d"

    print(f"\n{'='*78}")
    print(f"STRATEGY COMPARISON — {timeframe} candles, starting=${STARTING_BALANCE:.2f}, risk={RISK_PCT}%")
    print(f"Strategies: {', '.join(STRATEGY_FACTORIES.keys())}")
    print(f"Symbols:    {', '.join(SYMBOLS)}")
    print(f"{'='*78}")

    all_results: list[dict] = []

    # --- Per-symbol, per-strategy runs ---
    for symbol in SYMBOLS:
        closes = load_closes(symbol, timeframe)
        if closes is None:
            continue
        rows = []
        for name, factory in STRATEGY_FACTORIES.items():
            row = run_one(name, factory, symbol, closes, STARTING_BALANCE)
            rows.append(row)
            all_results.append(row)
        print_per_symbol_block(f"{symbol} ({len(closes)} candles)", rows)

    # --- Regime test (BTC split into N segments) ---
    regime_closes = load_closes(REGIME_TEST_SYMBOL, timeframe)
    regime_results: dict[str, dict[str, float]] = {}
    if regime_closes:
        segments = split_segments(regime_closes, REGIME_SEGMENTS)
        for name, factory in STRATEGY_FACTORIES.items():
            regime_results[name] = {}
            for i, seg in enumerate(segments):
                if len(seg) < 30:  # too short for any meaningful test
                    regime_results[name][f"Q{i+1}"] = 0.0
                    continue
                row = run_one(name, factory, f"{REGIME_TEST_SYMBOL}-Q{i+1}", seg, STARTING_BALANCE)
                regime_results[name][f"Q{i+1}"] = row.get("return_pct", 0.0) if "error" not in row else 0.0
        print_regime_block(regime_results)

    # --- Aggregate decision matrix ---
    by_strategy: dict[str, list[dict]] = {}
    for r in all_results:
        if "error" in r:
            continue
        by_strategy.setdefault(r["strategy"], []).append(r)

    decision_rows = []
    for name, results in by_strategy.items():
        total_trades = sum(r["trades"] for r in results)
        avg_win = (sum(r["win_rate_pct"] for r in results) / len(results)) if results else 0.0
        total_pnl = round(sum(r["pnl_usd"] for r in results), 3)
        avg_return = (sum(r["return_pct"] for r in results) / len(results)) if results else 0.0
        quarters = regime_results.get(name, {})
        positive_q = sum(1 for v in quarters.values() if v > 0)
        decision_rows.append({
            "strategy": name,
            "total_trades": total_trades,
            "avg_win_pct": round(avg_win, 2),
            "total_pnl": total_pnl,
            "avg_return_pct": round(avg_return, 3),
            "positive_quarters": positive_q,
            "quarters": quarters,
        })

    decision_rows.sort(key=lambda x: (x["positive_quarters"], x["total_pnl"]), reverse=True)
    print_decision_matrix(decision_rows)

    # --- Persist ---
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    out_path = DATA_DIR / f"comparison_{ts}.json"
    payload = {
        "timestamp": datetime.now().isoformat(),
        "timeframe": timeframe,
        "starting_balance": STARTING_BALANCE,
        "risk_pct": RISK_PCT,
        "horizon": HORIZON,
        "min_confidence": MIN_CONFIDENCE,
        "symbols": SYMBOLS,
        "strategies": list(STRATEGY_FACTORIES.keys()),
        "per_symbol_results": all_results,
        "regime_results": regime_results,
        "decision_matrix": decision_rows,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nFull results saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
