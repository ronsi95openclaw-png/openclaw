#!/usr/bin/env python3
"""
run_all.py — 4-Year TJR Backtest Full Pipeline
===============================================
Steps:
  1. Download 4-year daily + 2-year 1H data for ES and NQ (yfinance)
  2. Run TJR/ICT strategy on each combo: ES_1D, NQ_1D, ES_1H, NQ_1H
  3. Print comparison table
  4. Save results/4yr_backtest_results.json

Usage:
  python run_all.py
  python run_all.py --skip-fetch    (reuse existing data/*)
  python run_all.py --instrument ES --timeframe 1h
"""
from __future__ import annotations

import argparse
import json
import sys
import time as _time
from pathlib import Path
from datetime import datetime

# Make sure this folder is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import tjr_backtest_4yr
except ImportError as e:
    print(f"ERROR importing modules: {e}")
    sys.exit(1)

DATA_DIR    = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# -- Backtest Configs ----------------------------------------------------------

ALL_CONFIGS = [
    {
        "key":        "MES_1D",
        "instrument": "MES",          # Micro E-mini S&P -- $5/pt (1/10th of ES)
        "timeframe":  "1d",
        "csv":        DATA_DIR / "ES_1D.csv",   # Same price data as ES
        "contracts":  1,
        "lookback":   10,
        "sweep_bars": 3,
        "msb_bars":   3,
        "stop_ticks": 60,             # 60 x 0.25 = 15 pts x $5 = $75 risk/trade
    },
    {
        "key":        "MNQ_1D",
        "instrument": "MNQ",          # Micro E-mini Nasdaq -- $2/pt (1/10th of NQ)
        "timeframe":  "1d",
        "csv":        DATA_DIR / "NQ_1D.csv",   # Same price data as NQ
        "contracts":  1,
        "lookback":   10,
        "sweep_bars": 3,
        "msb_bars":   3,
        "stop_ticks": 150,            # 150 x 0.25 = 37.5 pts x $2 = $75 risk/trade
    },
    {
        "key":        "MES_1H",
        "instrument": "MES",          # Micro E-mini S&P -- $5/pt
        "timeframe":  "1h",
        "csv":        DATA_DIR / "ES_1H.csv",
        "contracts":  1,
        "kill_zones": ["ny_open"],
        "lookback":   16,
        "sweep_bars": 3,
        "msb_bars":   5,
        "stop_ticks": 60,             # 60 x 0.25 = 15 pts x $5 = $75 risk/trade
    },
    {
        "key":        "MNQ_1H",
        "instrument": "MNQ",          # Micro E-mini Nasdaq -- $2/pt
        "timeframe":  "1h",
        "csv":        DATA_DIR / "NQ_1H.csv",
        "contracts":  1,
        "kill_zones": ["ny_open"],
        "lookback":   16,
        "sweep_bars": 3,
        "msb_bars":   5,
        "stop_ticks": 80,             # 80 x 0.25 = 20 pts x $2 = $40 risk/trade  [v2: tightened]
        "daily_loss_limit": 200,      # stop today after $200 loss (~5 stops)      [v2: new]
    },
]

# -- Helpers -------------------------------------------------------------------

def print_comparison_table(all_results: dict):
    WIDTH = 90
    print("\n" + "=" * WIDTH)
    print("  4-YEAR TJR/ICT BACKTEST -- RESULTS COMPARISON")
    print("=" * WIDTH)

    hdr = f"{'Config':<10} {'Period':<22} {'Trades':>6} {'WinRate':>8} {'PF':>6} {'AvgR':>6} {'MaxDD':>10} {'NetPnL':>10} {'Lucid':>8}"
    print(hdr)
    print("-" * WIDTH)

    for key, res in all_results.items():
        if "error" in res and "performance" not in res:
            print(f"{key:<10}  ERROR: {res['error'][:60]}")
            continue

        p   = res.get("performance", {})
        r   = res.get("risk", {})
        lc  = res.get("lucid_compliance", {})
        e   = res.get("equity", {})
        per = res.get("period", {})

        period_str = f"{per.get('first_bar','?')} -> {per.get('last_bar','?')}"
        lucid_str  = "PASS" if lc.get("estimated_pass") else "FAIL"

        row = (
            f"{key:<10} "
            f"{period_str:<22} "
            f"{p.get('total_trades', 0):>6} "
            f"{p.get('win_rate_pct', 0.0):>7.1f}% "
            f"{p.get('profit_factor', 0.0):>6.2f} "
            f"{p.get('avg_r_multiple', 0.0):>5.2f}R "
            f"${r.get('max_drawdown_dollar', 0.0):>8.0f} "
            f"${e.get('net_change', 0.0):>9.0f} "
            f"{lucid_str:>8}"
        )
        print(row)

    print("=" * WIDTH)

    # Detail block
    print("\n-- LUCID 25K DETAIL ------------------------------------------------------------")
    for key, res in all_results.items():
        if "error" in res and "performance" not in res:
            continue
        lc = res.get("lucid_compliance", {})
        e  = res.get("equity", {})
        p  = res.get("performance", {})
        print(f"\n  {key}:")
        print(f"    Profit target ($1,500): {'MET' if lc.get('profit_target_met') else 'NOT MET'}  "
              f"(net ${e.get('net_change', 0):.0f})")
        print(f"    Max drawdown limit:     {'EXCEEDED' if lc.get('hit_max_loss') else 'SAFE'}  "
              f"(max ${res.get('risk', {}).get('max_drawdown_dollar', 0):.0f} / limit $1,500)")
        cv = lc.get("consistency_violations", 0)
        print(f"    Consistency rule:       {'VIOLATED' if cv else 'CLEAN'}  ({cv} violation(s))")
        print(f"    TP distribution:        TP1={p.get('tp1_exits',0)}  TP2={p.get('tp2_exits',0)}  "
              f"SL={p.get('sl_exits',0)}  EOD={p.get('eod_exits',0)}")
    print()


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="4-Year TJR Backtest Pipeline")
    parser.add_argument("--skip-fetch",  action="store_true", help="Skip data download (reuse existing CSVs)")
    parser.add_argument("--instrument",  default=None, choices=["ES", "NQ", "all"], help="Run only this instrument")
    parser.add_argument("--timeframe",   default=None, choices=["1d", "1h", "all"], help="Run only this timeframe")
    args = parser.parse_args()

    t0 = _time.time()
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n" + "=" * 60)
    print("   4-YEAR TJR BACKTEST PIPELINE  [v2 -- tighter risk + TP2 fix]")
    print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    # -- Step 1: Fetch data ----------------------------------------------------
    if not args.skip_fetch:
        print("\n[STEP 1/3] Downloading market data via yfinance...")
        try:
            import fetch_4yr_data
            fetch_4yr_data.main()
        except Exception as ex:
            print(f"\n  FATAL: Data fetch failed -- {ex}")
            print("  -> Try: python fetch_4yr_data.py to debug")
            sys.exit(1)
    else:
        print("\n[STEP 1/3] Skipping fetch (--skip-fetch mode)")
        csvs = list(DATA_DIR.glob("*.csv"))
        print(f"  Found {len(csvs)} existing CSV(s) in data/")

    # -- Step 2: Run backtests -------------------------------------------------
    print("\n[STEP 2/3] Running backtests...")

    # Filter configs
    configs = ALL_CONFIGS
    if args.instrument and args.instrument != "all":
        configs = [c for c in configs if c["instrument"] == args.instrument.upper()]
    if args.timeframe and args.timeframe != "all":
        configs = [c for c in configs if c["timeframe"] == args.timeframe.lower()]

    all_results = {}
    for cfg in configs:
        key = cfg["key"]
        csv_path = cfg["csv"]

        print(f"\n  [{key}] -------------------------------------------")
        if not csv_path.exists():
            print(f"  WARNING: CSV not found: {csv_path.name}  (run without --skip-fetch)")
            all_results[key] = {"error": f"CSV not found: {csv_path.name}",
                                "instrument": cfg["instrument"], "timeframe": cfg["timeframe"]}
            continue

        t1 = _time.time()
        try:
            bt_kwargs = {k: v for k, v in cfg.items()
                         if k not in ("key", "instrument", "timeframe", "csv", "contracts")}
            results = tjr_backtest_4yr.run_backtest(
                csv_path=csv_path,
                instrument=cfg["instrument"],
                timeframe=cfg["timeframe"],
                contracts=cfg.get("contracts", 1),
                **bt_kwargs,
            )
            elapsed = _time.time() - t1
            all_results[key] = results
            if "error" not in results:
                p  = results["performance"]
                lc = results["lucid_compliance"]
                print(f"  OK  {p['total_trades']} trades  "
                      f"WR={p['win_rate_pct']}%  "
                      f"PF={p['profit_factor']:.2f}  "
                      f"{'PASS' if lc['estimated_pass'] else 'FAIL'}  "
                      f"({elapsed:.1f}s)")
            else:
                print(f"  WARNING: {results['error']}")
        except Exception as ex:
            import traceback
            traceback.print_exc()
            all_results[key] = {"error": str(ex), "instrument": cfg["instrument"], "timeframe": cfg["timeframe"]}
            print(f"  ERROR: {ex}")

    # -- Step 3: Report --------------------------------------------------------
    print("\n[STEP 3/3] Results")
    print_comparison_table(all_results)

    # Save JSON
    outfile = RESULTS_DIR / f"4yr_backtest_{run_ts}.json"
    with open(outfile, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    elapsed_total = _time.time() - t0
    print(f"Done in {elapsed_total:.1f}s  ->  results/{outfile.name}")
    print()


if __name__ == "__main__":
    main()
