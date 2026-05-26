"""Regime-segmented strategy performance analysis.

Run: python scripts/analyze_ema_cross.py

Reads data/logs/trade_outcomes.jsonl and breaks down each strategy's
win rate by market regime. Use this before adjusting regime allowlists.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def analyze_strategy_by_regime(strategy_name: str = "EMA_CROSS") -> None:
    log_path = Path(__file__).parent.parent / "data" / "logs" / "trade_outcomes.jsonl"

    if not log_path.exists():
        print(f"No trade log at {log_path}")
        return

    results: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0})

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trade.get("strategy") != strategy_name:
                continue
            regime = trade.get("regime_label") or trade.get("regime") or "UNKNOWN"
            if trade.get("outcome") == "win":
                results[regime]["wins"] += 1
            else:
                results[regime]["losses"] += 1
            results[regime]["total_pnl"] += float(trade.get("pnl", 0))

    if not results:
        print(f"\n{strategy_name}: no trades in log yet.\n")
        return

    print(f"\n{'=' * 52}")
    print(f"STRATEGY: {strategy_name} — REGIME BREAKDOWN")
    print(f"{'=' * 52}")

    overall_wins = overall_total = 0
    good_regimes = []
    bad_regimes  = []

    for regime, data in sorted(results.items()):
        total = data["wins"] + data["losses"]
        wr    = data["wins"] / total if total else 0.0
        overall_wins  += data["wins"]
        overall_total += total
        if wr > 0.54:
            verdict      = "✅ KEEP"
            good_regimes.append(regime)
        elif wr > 0.45:
            verdict = "⚠️  MARGINAL"
        else:
            verdict     = "❌ DISABLE"
            bad_regimes.append(regime)
        print(f"\n{regime}")
        print(f"  Win rate : {wr:.1%}  ({data['wins']}W / {data['losses']}L / {total}T)")
        print(f"  Total P&L: ${data['total_pnl']:,.2f}")
        print(f"  Verdict  : {verdict}")

    if overall_total:
        overall_wr = overall_wins / overall_total
        print(f"\n{'=' * 52}")
        print(f"OVERALL: {overall_wr:.1%} WR across {overall_total} trades")
        print(f"{'=' * 52}")

    print("\nRECOMMENDATION:")
    if good_regimes:
        print(f"  Allow {strategy_name} in: {good_regimes}")
    if bad_regimes:
        print(f"  Block {strategy_name} in: {bad_regimes}")
    print()


if __name__ == "__main__":
    targets = sys.argv[1:] or ["EMA_CROSS", "TREND_FOLLOW", "RSI_MEAN_REVERT",
                                "BREAKOUT", "BOLLINGER_BAND", "DCA", "VWAP"]
    for strat in targets:
        analyze_strategy_by_regime(strat)
