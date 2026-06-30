#!/usr/bin/env python3
"""
smoke_test.py — Quick sanity check for the 4-year TJR backtest
===============================================================
Generates synthetic OHLCV bars (trending/ranging/volatile regimes)
and verifies:
  1. Bars parse correctly
  2. Strategy generates ≥1 trade
  3. Trade accounting is self-consistent (equity = account + sum(pnl))
  4. Lucid mandate fields are all present
  5. No Python exceptions

Usage:
  python smoke_test.py
  python smoke_test.py --verbose
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import tempfile
import csv as _csv
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tjr_backtest_4yr as bt4

# ── Synthetic Bar Generator ───────────────────────────────────────────────────

def make_synthetic_bars(n: int = 300, seed: int = 42) -> list[bt4.Bar]:
    """
    Generate synthetic 1H-style OHLCV bars with trend + noise + sweeps.
    Price starts around ES 4000. Periodic liquidity sweeps are injected.
    """
    rng = random.Random(seed)
    bars = []
    price = 4000.0
    dt = datetime(2024, 1, 2, 9, 0)   # Start: Jan 2 2024, 9 AM ET

    trend = 0.0
    for i in range(n):
        # Advance time (skip weekends roughly)
        dt += timedelta(hours=1)
        if dt.weekday() >= 5:   # sat/sun
            continue

        # Regime change every ~60 bars
        if i % 60 == 0:
            trend = rng.uniform(-0.3, 0.3)

        # Inject periodic sweep: sudden spike beyond prior range, then reverse
        if i % 40 == 39:
            spike_dir = rng.choice([-1, 1])
            spike_mag = rng.uniform(8, 20)
            op = price
            if spike_dir > 0:
                hi = price + spike_mag
                lo = price - 1.0
            else:
                hi = price + 1.0
                lo = price - spike_mag
            cl = price - spike_dir * spike_mag * 0.7   # close-back
            price = cl
        else:
            move = trend + rng.gauss(0, 1.5)
            op = price
            hi = op + abs(rng.gauss(0, 1.5)) + max(move, 0)
            lo = op - abs(rng.gauss(0, 1.5)) + min(move, 0)
            cl = op + move
            price = cl

        bars.append(bt4.Bar(
            dt=dt, open=round(op, 2), high=round(hi, 2),
            low=round(lo, 2), close=round(cl, 2), volume=rng.randint(10000, 50000),
        ))

    return bars


def bars_to_csv(bars: list[bt4.Bar]) -> Path:
    """Write synthetic bars to a temp CSV and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    w = _csv.writer(tmp)
    w.writerow(["Datetime", "Open", "High", "Low", "Close", "Volume"])
    for b in bars:
        w.writerow([b.dt.strftime("%Y-%m-%d %H:%M:%S"),
                    b.open, b.high, b.low, b.close, int(b.volume)])
    tmp.close()
    return Path(tmp.name)

# ── Tests ─────────────────────────────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"

def run_tests(verbose: bool = False) -> int:
    """Return number of failures."""
    failures = 0
    bars = make_synthetic_bars(300)
    csv_path = bars_to_csv(bars)

    try:
        # ── Test 1: CSV parsing round-trip ────────────────────────────────────
        name = "CSV parse round-trip"
        parsed = bt4.parse_yfinance_csv(csv_path)
        ok = len(parsed) == len(bars) and all(
            abs(p.open - b.open) < 0.01 for p, b in zip(parsed, bars)
        )
        status = PASS if ok else FAIL
        print(f"  {status}  {name}  ({len(parsed)} bars)")
        if not ok: failures += 1

        # ── Test 2: Strategy generates ≥1 trade (1H) ─────────────────────────
        name = "Strategy finds ≥1 trade (1H)"
        backtester = bt4.TJRBacktester4yr(instrument="ES", timeframe="1h",
                                           kill_zones=[], lookback=8, sweep_bars=2, msb_bars=3)
        results = backtester.run(parsed)
        n_trades = results.get("performance", {}).get("total_trades", 0)
        ok = n_trades >= 1
        status = PASS if ok else FAIL
        print(f"  {status}  {name}  (generated {n_trades} trades)")
        if not ok: failures += 1

        # ── Test 3: Equity accounting ─────────────────────────────────────────
        name = "Equity accounting consistent"
        if n_trades >= 1:
            expected_equity = backtester.account_size + sum(t.pnl for t in backtester.trades)
            actual_equity   = backtester.equity
            ok = abs(expected_equity - actual_equity) < 0.01
            status = PASS if ok else FAIL
            print(f"  {status}  {name}  (Δ={expected_equity - actual_equity:.4f})")
            if not ok: failures += 1
        else:
            print(f"  ⏭️  SKIP  {name}  (no trades)")

        # ── Test 4: Result keys present ───────────────────────────────────────
        name = "Result dict has all required keys"
        required = ["performance", "risk", "lucid_compliance", "equity", "period", "strategy"]
        ok = all(k in results for k in required)
        missing = [k for k in required if k not in results]
        status = PASS if ok else FAIL
        print(f"  {status}  {name}  (missing: {missing or 'none'})")
        if not ok: failures += 1

        # ── Test 5: Lucid compliance fields ───────────────────────────────────
        name = "Lucid compliance fields present"
        lc = results.get("lucid_compliance", {})
        lc_required = ["profit_target_met", "hit_max_loss", "consistency_violations", "estimated_pass"]
        ok = all(k in lc for k in lc_required)
        status = PASS if ok else FAIL
        print(f"  {status}  {name}")
        if not ok: failures += 1

        # ── Test 6: Daily timeframe runs without exception ────────────────────
        name = "Daily (1D) timeframe runs clean"
        try:
            bt_daily = bt4.TJRBacktester4yr(instrument="ES", timeframe="1d",
                                             lookback=5, sweep_bars=2, msb_bars=2)
            res_daily = bt_daily.run(parsed)
            ok = "strategy" in res_daily or "error" in res_daily
            status = PASS if ok else FAIL
            extra = f"({res_daily.get('performance', {}).get('total_trades', 0)} trades)"
        except Exception as ex:
            ok = False
            status = FAIL
            extra = f"Exception: {ex}"
        print(f"  {status}  {name}  {extra}")
        if not ok: failures += 1

        # ── Test 7: No open trade at end ──────────────────────────────────────
        name = "No open trade left at data end"
        ok = backtester.open_trade is None
        status = PASS if ok else FAIL
        print(f"  {status}  {name}")
        if not ok: failures += 1

        if verbose and n_trades >= 1:
            p = results["performance"]
            r = results["risk"]
            print(f"\n  Verbose sample (1H synthetic):")
            print(f"    Trades={p['total_trades']}  WR={p['win_rate_pct']}%  PF={p['profit_factor']:.2f}")
            print(f"    MaxDD=${r['max_drawdown_dollar']:.2f}  NetPnL=${results['equity']['net_change']:.2f}")

    finally:
        csv_path.unlink(missing_ok=True)   # cleanup temp file

    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("\n" + "=" * 55)
    print("  TJR 4yr Backtest — Smoke Test")
    print("=" * 55)

    failures = run_tests(args.verbose)

    print("\n" + ("─" * 55))
    if failures == 0:
        print("  ✅ All tests passed — ready to run on live data")
    else:
        print(f"  ❌ {failures} test(s) FAILED — fix before proceeding")
    print("─" * 55 + "\n")

    sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    main()
