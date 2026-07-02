#!/usr/bin/env python3
"""
strategy_reviewer.py — Loss analysis and parameter sweep for the TJR bot.

Reads backtest output, explains why each trade lost, and sweeps strategy
parameters to find which combinations perform better on the same data.

Usage:
  python -m bot.strategy_reviewer analyze --csv backtest/data/ES_5M.csv
  python -m bot.strategy_reviewer analyze --json bot/logs/backtests/bt_ES_*.json
  python -m bot.strategy_reviewer sweep   --csv backtest/data/ES_5M.csv
  python -m bot.strategy_reviewer report

Commands:
  analyze  Run a fresh backtest (or load a saved JSON), explain each losing
           trade, write bot/logs/loss_analysis.jsonl
  sweep    Run the backtest across a parameter grid; rank by Sharpe/P&L.
           Writes bot/logs/param_sweep.json
  report   Print a summary from existing loss_analysis.jsonl + param_sweep.json

Output files (relative to vibe-trading/bot/logs/):
  loss_analysis.jsonl  — one entry per losing trade with "why_lost" field
  param_sweep.json     — ranked parameter combinations
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from copy import copy
from datetime import datetime, time
from pathlib import Path
from typing import Optional

BOT_DIR = Path(__file__).resolve().parent
VIBE_DIR = BOT_DIR.parent
LOG_DIR = BOT_DIR / "logs"
LOSS_LOG = LOG_DIR / "loss_analysis.jsonl"
SWEEP_LOG = LOG_DIR / "param_sweep.json"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Kill zone bounds (ET) — mirrors backtest.KILL_ZONES ───────────────────────
_KILL_ZONES: dict[str, tuple[time, time]] = {
    "ny_open":      (time(8, 30),  time(11, 0)),
    "london_open":  (time(2, 0),   time(5, 0)),
    "ny_pm":        (time(13, 30), time(16, 0)),
    "london_close": (time(10, 0),  time(12, 0)),
}


# ── Loss reason generator ─────────────────────────────────────────────────────

def _minutes_into_kz(entry_dt: datetime, kz_name: str) -> Optional[int]:
    if kz_name not in _KILL_ZONES:
        return None
    lo, _ = _KILL_ZONES[kz_name]
    t = entry_dt.time()
    return (t.hour * 60 + t.minute) - (lo.hour * 60 + lo.minute)


def _why_lost(trade: dict) -> list[str]:
    """Generate plain-English diagnoses for a stopped-out or flat-exit trade."""
    reasons: list[str] = []
    status = trade.get("status", "")
    bars = int(trade.get("bars_held", 0))
    r_ticks = int(trade.get("r_ticks", 0))
    kz = trade.get("kill_zone", "none")
    pnl = float(trade.get("pnl", 0))
    r_mult = float(trade.get("r_multiple", 0))
    entry_dt_str = trade.get("entry_dt", "")

    try:
        entry_dt = datetime.fromisoformat(entry_dt_str)
        mins_in = _minutes_into_kz(entry_dt, kz)
    except Exception:
        mins_in = None

    if status == "sl":
        reasons.append(f"Stopped out at exactly -1R (${abs(pnl):.0f})")

        if bars <= 3:
            reasons.append(
                f"Stopped in {bars} bars — very fast stop. "
                "Entry timing may be off (entered into momentum, not after a pull-back)."
            )
        elif bars <= 8:
            reasons.append(
                f"Stopped in {bars} bars — moderate speed. "
                "Price reversed quickly after entry; entry was likely too early in the FVG."
            )
        else:
            reasons.append(
                f"Stopped after {bars} bars — trade had time but ultimately failed. "
                "Could be a structural breakdown, not a timing issue."
            )

        if r_ticks <= 6:
            reasons.append(
                f"Stop was only {r_ticks} ticks ({r_ticks * 0.25:.2f} pts). "
                "Very tight stop — micro noise can trigger it. "
                "Consider min stop_ticks >= 8-10 on ES."
            )
        elif r_ticks >= 16:
            reasons.append(
                f"Stop was {r_ticks} ticks ({r_ticks * 0.25:.2f} pts) — wide. "
                "Wide stops increase risk per trade; if winning, check if TP2 also hits."
            )

        if mins_in is not None:
            if mins_in > 90:
                reasons.append(
                    f"Entry was {mins_in}m into the {kz} window. "
                    "Late-kill-zone entries tend to chase extended moves. "
                    "Consider filtering entries > 75m into any kill zone."
                )
            elif mins_in < 5:
                reasons.append(
                    f"Entry was {mins_in}m into {kz} — very early. "
                    "Opening-bell liquidity can be choppy before the real move develops. "
                    "Waiting 5-10 min after open may improve quality."
                )

    elif status == "eod":
        reasons.append(
            f"EOD flatten at 15:55 ET with P&L ${pnl:.0f}. "
            "Trade was still open at session close — no decisive fill. "
            "Check if price was stuck between S/R levels all day."
        )

    elif status in ("tp1", "tp2"):
        reasons.append(
            f"Winner: {status.upper()} exit, R={r_mult:.2f}, P&L=${pnl:.0f}. "
            "No improvement needed for this trade."
        )

    return reasons


# ── Run a backtest and return results dict ────────────────────────────────────

def _run_bt(csv_path: str, instrument: str = "ES",
            strategy_config=None) -> dict:
    from bot.backtest import BotBacktester, load_bars_csv
    bars = load_bars_csv(Path(csv_path))
    bt = BotBacktester(instrument=instrument)
    if strategy_config is not None:
        bt._strategy_config_override = strategy_config
        # Sync zones to the override config's kill_zones so the fallback path also
        # respects them.
        kz = getattr(strategy_config, "kill_zones", None)
        if kz:
            bt.zones = list(kz)
    return bt.run(bars)


# ── analyze command ───────────────────────────────────────────────────────────

def cmd_analyze(csv_path: Optional[str], json_path: Optional[str],
                instrument: str) -> None:
    if json_path:
        p = Path(json_path)
        if not p.exists():
            print(f"ERROR: {json_path} not found.")
            sys.exit(1)
        res = json.loads(p.read_text(encoding="utf-8"))
        print(f"Loaded backtest from {json_path}")
    elif csv_path:
        print(f"Running backtest on {csv_path} …")
        res = _run_bt(csv_path, instrument)
    else:
        # Try to find the most recent backtest JSON
        bt_dir = LOG_DIR / "backtests"
        candidates = sorted(bt_dir.glob("bt_*.json")) if bt_dir.exists() else []
        if not candidates:
            print("ERROR: provide --csv or --json, or run a backtest first.")
            sys.exit(1)
        p = candidates[-1]
        res = json.loads(p.read_text(encoding="utf-8"))
        print(f"Loaded most recent backtest: {p.name}")

    if "error" in res:
        print(f"Backtest had no trades: {res['error']}")
        sys.exit(0)

    trades = res.get("trades", [])
    if not trades:
        print("No per-trade data in result (backtest may be from an older version).")
        sys.exit(0)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    losers = [t for t in trades if t["pnl"] <= 0]
    winners = [t for t in trades if t["pnl"] > 0]
    print(f"\n{'='*60}")
    print(f"  Strategy Loss Analysis — {len(trades)} trades "
          f"({len(winners)} wins / {len(losers)} losses)")
    print(f"{'='*60}")

    entries = []
    for t in trades:
        diags = _why_lost(t)
        entry = {**t, "why_lost": diags}
        entries.append(entry)

        icon = "✓" if t["pnl"] > 0 else "✗"
        print(f"\n{icon} {t['entry_dt'][:16]}  {t['side'].upper():<5}  "
              f"entry={t['entry']}  exit={t['exit']}  "
              f"status={t['status']}  P&L=${t['pnl']:+.0f}  "
              f"R={t['r_multiple']:.2f}  bars={t.get('bars_held','?')}  kz={t.get('kill_zone','?')}")
        for d in diags:
            print(f"    → {d}")

    # Write loss_analysis.jsonl
    with open(LOSS_LOG, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, default=str) + "\n")
    print(f"\n→ Loss analysis written to {LOSS_LOG}")

    # Pattern summary
    if losers:
        avg_bars = sum(t.get("bars_held", 0) for t in losers) / len(losers)
        avg_r_ticks = sum(t.get("r_ticks", 0) for t in losers) / len(losers)
        kz_counts: dict[str, int] = {}
        for t in losers:
            kz_counts[t.get("kill_zone", "none")] = kz_counts.get(t.get("kill_zone", "none"), 0) + 1
        print(f"\n{'─'*60}")
        print(f"  LOSS PATTERN SUMMARY")
        print(f"  Avg bars before stop:  {avg_bars:.1f}")
        print(f"  Avg stop width:        {avg_r_ticks:.1f} ticks ({avg_r_ticks*0.25:.2f} pts)")
        print(f"  Kill zones of losses:  {kz_counts}")
        print(f"{'─'*60}")
        print("\n  Suggestions:")
        if avg_bars < 5:
            print("  • Fast stops (<5 bars): try wider stop_ticks (10-12) or wait for deeper OTE pull-back")
        if avg_r_ticks < 8:
            print("  • Tight stops (<8 ticks): you may be getting stopped by noise; try stop_ticks=10")
        if len(set(t.get("kill_zone") for t in losers)) == 1:
            only = losers[0].get("kill_zone", "?")
            print(f"  • All losses in '{only}' kill zone — test disabling it or adding a time filter")


# ── sweep command ─────────────────────────────────────────────────────────────

_GRID = {
    "stop_ticks":  [6, 8, 10, 12],
    "ote_low":     [0.5, 0.618, 0.7],
    "sweep_bars":  [2, 3, 5],
    "lookback":    [15, 20],
}


def _grid_combos() -> list[dict]:
    from itertools import product
    keys = list(_GRID.keys())
    combos = []
    for vals in product(*[_GRID[k] for k in keys]):
        combos.append(dict(zip(keys, vals)))
    return combos


def cmd_sweep(csv_path: str, instrument: str) -> None:
    try:
        from bot.strategy import StrategyConfig
        from bot.backtest import load_bars_csv
    except Exception as e:
        print(f"ERROR importing bot modules: {e}")
        sys.exit(1)

    bars_path = Path(csv_path)
    if not bars_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)

    combos = _grid_combos()
    total = len(combos)
    print(f"Parameter sweep: {total} combinations on {bars_path.name}")
    print(f"Grid: {_GRID}\n")

    results = []
    for idx, combo in enumerate(combos, 1):
        sc = StrategyConfig(
            stop_ticks=combo["stop_ticks"],
            ote_low=combo["ote_low"],
            sweep_bars=combo["sweep_bars"],
            lookback=combo["lookback"],
        )
        try:
            res = _run_bt(csv_path, instrument, strategy_config=sc)
        except Exception as e:
            print(f"  [{idx:3d}/{total}] ERROR: {e}")
            continue

        if "error" in res:
            trades_n = 0
            pnl = 0.0
            sharpe = 0.0
            wr = 0.0
            dd = 0.0
        else:
            perf = res["performance"]
            risk = res["risk"]
            trades_n = perf["total_trades"]
            pnl = perf["total_pnl"]
            sharpe = perf["sharpe_per_trade"]
            wr = perf["win_rate_pct"]
            dd = risk["max_drawdown_dollar"]

        row = {
            **combo,
            "trades": trades_n,
            "pnl": round(pnl, 2),
            "sharpe": round(sharpe, 3),
            "win_rate_pct": round(wr, 1),
            "max_drawdown": round(dd, 2),
        }
        results.append(row)
        status = f"P&L=${pnl:+.0f}  trades={trades_n}  WR={wr:.0f}%  sharpe={sharpe:.3f}"
        print(f"  [{idx:3d}/{total}] {combo}  →  {status}")

    if not results:
        print("No results produced.")
        sys.exit(1)

    # Rank: primary = P&L desc, secondary = Sharpe desc, tertiary = fewest trades (tight filter)
    ranked = sorted(results,
                    key=lambda r: (r["pnl"], r["sharpe"], -r["trades"]),
                    reverse=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "grid": _GRID,
        "instrument": instrument,
        "csv": str(bars_path),
        "total_combos": total,
        "ranked": ranked,
    }
    SWEEP_LOG.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n→ Param sweep written to {SWEEP_LOG}")

    print(f"\n{'='*70}")
    print(f"  TOP 10 PARAMETER COMBINATIONS")
    print(f"{'='*70}")
    print(f"  {'stop_tks':>8}  {'ote_low':>7}  {'swp_b':>5}  {'lkbk':>4}  "
          f"{'trades':>6}  {'P&L':>8}  {'WR%':>4}  {'sharpe':>6}  {'dd':>8}")
    print(f"  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*4}  "
          f"{'-'*6}  {'-'*8}  {'-'*4}  {'-'*6}  {'-'*8}")
    for r in ranked[:10]:
        print(f"  {r['stop_ticks']:>8}  {r['ote_low']:>7.3f}  {r['sweep_bars']:>5}  "
              f"{r['lookback']:>4}  {r['trades']:>6}  ${r['pnl']:>7.0f}  "
              f"{r['win_rate_pct']:>4.0f}  {r['sharpe']:>6.3f}  ${r['max_drawdown']:>7.0f}")

    if ranked:
        best = ranked[0]
        print(f"\n  RECOMMENDATION:")
        print(f"  Best combo by P&L+Sharpe:")
        print(f"    stop_ticks={best['stop_ticks']}  ote_low={best['ote_low']}  "
              f"sweep_bars={best['sweep_bars']}  lookback={best['lookback']}")
        print(f"    → P&L=${best['pnl']:+.0f}  WR={best['win_rate_pct']:.0f}%  "
              f"Sharpe={best['sharpe']:.3f}  trades={best['trades']}")
        print(f"\n  To apply: edit bot/config.py StrategyConfig defaults to these values.")
        print(f"  Re-verify with: python -m bot.backtest <csv> --instrument {instrument}")


# ── report command ────────────────────────────────────────────────────────────

def cmd_report() -> None:
    print(f"\n{'='*60}")
    print(f"  STRATEGY REVIEW REPORT")
    print(f"{'='*60}")

    if LOSS_LOG.exists():
        entries = []
        for line in LOSS_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        losers = [e for e in entries if e.get("pnl", 0) <= 0]
        winners = [e for e in entries if e.get("pnl", 0) > 0]
        print(f"\nLoss Analysis ({LOSS_LOG.name}):")
        print(f"  {len(entries)} trades  |  {len(winners)} wins  |  {len(losers)} losses")
        for e in entries:
            icon = "✓" if e["pnl"] > 0 else "✗"
            print(f"  {icon} {e['entry_dt'][:16]}  {e['side'].upper()}  "
                  f"P&L=${e['pnl']:+.0f}  bars={e.get('bars_held','?')}  kz={e.get('kill_zone','?')}")
            for d in e.get("why_lost", []):
                print(f"      {d}")
    else:
        print(f"\nNo loss analysis yet. Run:  python -m bot.strategy_reviewer analyze --csv <path>")

    if SWEEP_LOG.exists():
        sw = json.loads(SWEEP_LOG.read_text(encoding="utf-8"))
        ranked = sw.get("ranked", [])
        if ranked:
            best = ranked[0]
            print(f"\nParam Sweep ({SWEEP_LOG.name}):")
            print(f"  {sw.get('total_combos', '?')} combos tested on {Path(sw.get('csv','')).name}")
            print(f"  Best: stop_ticks={best['stop_ticks']}  ote_low={best['ote_low']}  "
                  f"sweep_bars={best['sweep_bars']}  lookback={best['lookback']}")
            print(f"        P&L=${best['pnl']:+.0f}  WR={best['win_rate_pct']:.0f}%  "
                  f"Sharpe={best['sharpe']:.3f}  trades={best['trades']}")
    else:
        print(f"\nNo sweep yet. Run:  python -m bot.strategy_reviewer sweep --csv <path>")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TJR bot strategy reviewer: loss analysis + parameter sweep")
    sub = parser.add_subparsers(dest="cmd")

    p_analyze = sub.add_parser("analyze", help="Explain why each trade lost")
    p_analyze.add_argument("--csv", default=None, help="Path to NinjaTrader CSV")
    p_analyze.add_argument("--json", default=None, help="Path to saved backtest JSON")
    p_analyze.add_argument("--instrument", default="ES")

    p_sweep = sub.add_parser("sweep", help="Parameter grid search")
    p_sweep.add_argument("--csv", required=True, help="Path to NinjaTrader CSV")
    p_sweep.add_argument("--instrument", default="ES")

    sub.add_parser("report", help="Print existing analysis + sweep results")

    args = parser.parse_args(argv)

    if args.cmd == "analyze":
        cmd_analyze(args.csv, args.json, args.instrument)
    elif args.cmd == "sweep":
        cmd_sweep(args.csv, args.instrument)
    elif args.cmd == "report":
        cmd_report()
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
