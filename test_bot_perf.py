"""Quick performance test — runs the bot in demo mode for N scan cycles,
then prints a formatted performance report. No Google Sheets, no real orders.

Usage:
    python test_bot_perf.py [--cycles N] [--interval S]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--cycles",   type=int, default=5,  help="Number of scan cycles (default 5)")
parser.add_argument("--interval", type=int, default=5,  help="Seconds between scans (default 5)")
args = parser.parse_args()

# ── Logging: show INFO only from our bot, suppress noisy libs ──────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-7s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logging.getLogger("clawbot").setLevel(logging.INFO)

# ── Boot bot ──────────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("  BloFin Trading Bot — Demo Performance Test")
print(f"  Cycles: {args.cycles}   Interval: {args.interval}s   "
      f"Started: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
print("="*65 + "\n")

from trading.blofin_bot import BloFinBot

bot = BloFinBot()
bot.configure(demo_mode=True)
bot.state.scan_interval = args.interval

# Run scans manually so we control the loop and can print after each cycle
for cycle in range(1, args.cycles + 1):
    print(f"── Cycle {cycle}/{args.cycles} ──────────────────────────────────────────")
    bot._scan()
    s = bot.get_status()

    print(f"  Status   : {s['status_msg']}")
    print(f"  Balance  : ${s['balance']:,.2f}   PnL: {s['total_pnl']:+.4f} USDT")
    print(f"  Trades/day: {s['trades_today']}   Open positions: {len(s['open_positions'])}")

    if s["open_positions"]:
        print("  Open positions:")
        for p in s["open_positions"]:
            print(f"    [{p['strategy']:<16}] {p['side'].upper():<5} {p['symbol']:<10} "
                  f"entry={p['entry_price']:,.2f}  cur={p['current_price']:,.2f}  "
                  f"uPnL={p['unrealized_pnl']:+.4f}  conf={p['confidence']}%  "
                  f"regime={p.get('regime_label','?')}")

    if cycle < args.cycles:
        print(f"  (waiting {args.interval}s…)\n")
        time.sleep(args.interval)

# ── Final report ──────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("  FINAL PERFORMANCE REPORT")
print("="*65)

s = bot.get_status()

print(f"\n  Demo mode : {s['demo_mode']}")
print(f"  Balance   : ${s['balance']:,.2f}")
print(f"  Total PnL : {s['total_pnl']:+.4f} USDT")
print(f"  Unrealised: {s['unrealized_pnl']:+.4f} USDT")
print(f"  Trades    : {s['trades_today']} opened today")

print("\n  Strategy Weights & Stats")
print(f"  {'Strategy':<18} {'Weight':>6}  {'Trades':>6}  {'Wins':>5}  {'Losses':>6}  {'WinRate':>7}")
print("  " + "-"*56)
for name, w in s["strategy_weights"].items():
    print(f"  {name:<18} {w['weight']:>6.2f}  {w['trades']:>6}  {w['wins']:>5}  "
          f"{w['losses']:>6}  {w['win_rate']:>6.1f}%")

if s["trade_log"]:
    print(f"\n  Closed Trades (last {len(s['trade_log'])})")
    print(f"  {'Strategy':<16}  {'Symbol':<10}  {'Side':<5}  {'PnL':>8}  {'Outcome'}")
    print("  " + "-"*55)
    for t in s["trade_log"]:
        print(f"  {t['strategy']:<16}  {t['symbol']:<10}  {t['side']:<5}  "
              f"{t['pnl']:>+8.4f}  {t['outcome'].upper()}")
    wins   = sum(1 for t in s["trade_log"] if t["outcome"] == "win")
    losses = sum(1 for t in s["trade_log"] if t["outcome"] == "loss")
    total  = wins + losses
    if total:
        print(f"\n  Closed: {total}  |  Wins: {wins}  |  Losses: {losses}  "
              f"|  WR: {wins/total*100:.0f}%")
        total_pnl = sum(t["pnl"] for t in s["trade_log"])
        print(f"  Realised PnL from closed trades: {total_pnl:+.4f} USDT")
else:
    print("\n  No closed trades yet (positions may still be open).")

if s["open_positions"]:
    print(f"\n  Open Positions ({len(s['open_positions'])})")
    for p in s["open_positions"]:
        print(f"  [{p['strategy']:<16}] {p['side'].upper():<5} {p['symbol']:<10} "
              f"entry={p['entry_price']:,.2f}  cur={p['current_price']:,.2f}  "
              f"uPnL={p['unrealized_pnl']:+.4f}  SL={p['sl_price']:,.2f}  TP={p['tp_price']:,.2f}")

print("\n" + "="*65 + "\n")
