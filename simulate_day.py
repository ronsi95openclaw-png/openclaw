"""Full-day bot simulation — 96 cycles = 24 hours of 15-min candles.

Runs in demo mode with no sleep delays. Produces an hourly breakdown,
per-strategy stats, drawdown analysis, and final P&L summary.
"""
from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
logging.getLogger("clawbot").setLevel(logging.WARNING)   # suppress per-trade noise

CYCLES        = 96      # 96 × 15 min = 24 h
STARTING_BAL  = 1000.0  # fresh account

# ── Reset bot state for a clean run ──────────────────────────────────────────
from trading.sim_engine import BloFinBot, _STATE_FILE
from trading.strategies import _WEIGHTS_FILE
import json, pathlib

def reset_state():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({
        "demo_mode": True, "risk_pct": 1.5,
        "total_pnl": 0.0, "trades_date": "", "trades_today": 0, "trade_log": [],
    }, indent=2))
    _WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WEIGHTS_FILE.write_text(json.dumps({
        s: {"trades": 0, "wins": 0, "losses": 0, "weight": 1.0}
        for s in ["EMA_CROSS", "RSI_MEAN_REVERT", "BREAKOUT", "FUNDING_ARB"]
    }, indent=2))

reset_state()

# Rotate journal so each sim run gets its own file
from trading.sim_engine import _JOURNAL_FILE
_JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
_run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
_SIM_JOURNAL = _JOURNAL_FILE.parent / f"signal_journal_{_run_ts}.jsonl"
# Monkey-patch the module-level path so the bot writes to our dated file
import trading.sim_engine as _bot_mod
_bot_mod._JOURNAL_FILE = _SIM_JOURNAL

bot = BloFinBot()
bot.configure(demo_mode=True, risk_pct=1.5)
bot.state.balance = STARTING_BAL

# ── Tracking structures ───────────────────────────────────────────────────────
hourly: list[dict]           = []
strategy_trades: dict        = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
symbol_trades: dict          = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
regime_trades: dict          = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
equity_curve: list[float]    = [STARTING_BAL]
all_closed: list[dict]       = []
blocked_count: int           = 0

peak_equity    = STARTING_BAL
max_drawdown   = 0.0
start_time     = time.time()
snapshot_log   = []   # one row per cycle for the equity curve

print("\n" + "="*70)
print("  BloFin Bot — Full Day Simulation  (96 cycles × 15 min = 24 h)")
print(f"  Starting balance: ${STARTING_BAL:,.2f}   Risk: 1.5%   Leverage: 3×")
print("="*70)
print(f"  {'Hour':>4}  {'Cycle':>5}  {'Balance':>10}  {'PnL':>10}  "
      f"{'Trades':>6}  {'Open':>4}  {'Status'}")
print("  " + "-"*68)

prev_closed_count = 0

for cycle in range(1, CYCLES + 1):
    bot._scan()
    s = bot.get_status()

    equity = s["balance"] + s["unrealized_pnl"]
    equity_curve.append(equity)

    # Track drawdown
    if equity > peak_equity:
        peak_equity = equity
    dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
    if dd > max_drawdown:
        max_drawdown = dd

    # Harvest newly closed trades from trade_log
    new_closed = s["trade_log"]
    newly_closed = new_closed[:len(new_closed) - prev_closed_count] if len(new_closed) > prev_closed_count else []
    for t in newly_closed:
        strategy_trades[t["strategy"]]["wins" if t["outcome"] == "win" else "losses"] += 1
        strategy_trades[t["strategy"]]["pnl"] += t["pnl"]
        symbol_trades[t["symbol"]]["wins" if t["outcome"] == "win" else "losses"] += 1
        symbol_trades[t["symbol"]]["pnl"] += t["pnl"]
        r = t.get("regime_label", "UNKNOWN")
        regime_trades[r]["wins" if t["outcome"] == "win" else "losses"] += 1
        regime_trades[r]["pnl"] += t["pnl"]
        all_closed.append(t)
    prev_closed_count = len(new_closed)

    snapshot_log.append({
        "cycle": cycle, "equity": round(equity, 2),
        "pnl": round(s["total_pnl"], 4), "open": len(s["open_positions"]),
    })

    # Print every 4 cycles (= 1 simulated hour)
    hour = cycle // 4
    if cycle % 4 == 0 or cycle == CYCLES:
        total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
        print(f"  {hour:>4}h  {cycle:>5}  ${equity:>9,.2f}  "
              f"{s['total_pnl']:>+9.2f}  {total_closed:>6}  "
              f"{len(s['open_positions']):>4}  {s['status_msg'][:35]}")

elapsed = time.time() - start_time

# ── Final report ──────────────────────────────────────────────────────────────
s = bot.get_status()
final_equity = s["balance"] + s["unrealized_pnl"]
total_return = (final_equity - STARTING_BAL) / STARTING_BAL * 100
total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
total_wins   = sum(v["wins"]   for v in strategy_trades.values())
total_losses = sum(v["losses"] for v in strategy_trades.values())
overall_wr   = total_wins / total_closed * 100 if total_closed else 0.0

print("\n" + "="*70)
print("  SIMULATION COMPLETE")
print("="*70)
print(f"\n  Simulated period : 24 hours (96 × 15-min cycles)")
print(f"  Wall-clock time  : {elapsed:.1f}s")
print(f"\n  Starting balance : ${STARTING_BAL:,.2f}")
print(f"  Final equity     : ${final_equity:,.2f}  ({total_return:+.2f}%)")
print(f"  Realised PnL     : {s['total_pnl']:+.4f} USDT")
print(f"  Unrealised PnL   : {s['unrealized_pnl']:+.4f} USDT")
print(f"  Peak equity      : ${peak_equity:,.2f}")
print(f"  Max drawdown     : {max_drawdown:.2f}%")

print(f"\n  Total trades     : {total_closed}")
print(f"  Wins / Losses    : {total_wins} / {total_losses}")
print(f"  Win rate         : {overall_wr:.1f}%")
if total_closed:
    avg_win  = sum(t["pnl"] for t in all_closed if t["outcome"] == "win")  / max(total_wins, 1)
    avg_loss = sum(t["pnl"] for t in all_closed if t["outcome"] == "loss") / max(total_losses, 1)
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    print(f"  Avg win          : {avg_win:+.4f} USDT")
    print(f"  Avg loss         : {avg_loss:+.4f} USDT")
    print(f"  Reward/Risk      : {rr:.2f}×")
    expectancy = (overall_wr/100 * avg_win) + ((1 - overall_wr/100) * avg_loss)
    print(f"  Expectancy/trade : {expectancy:+.4f} USDT")

# ── Per-strategy breakdown ────────────────────────────────────────────────────
print(f"\n  ── Strategy Breakdown {'─'*47}")
print(f"  {'Strategy':<18} {'Trades':>6}  {'Wins':>5}  {'Losses':>6}  "
      f"{'WR%':>5}  {'PnL':>9}  {'Weight':>6}")
print("  " + "-"*62)
for name, sw in s["strategy_weights"].items():
    st = strategy_trades[name]
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {name:<18} {ct:>6}  {st['wins']:>5}  {st['losses']:>6}  "
          f"{wr:>5.1f}  {st['pnl']:>+9.4f}  {sw['weight']:>6.2f}")

# ── Per-symbol breakdown ──────────────────────────────────────────────────────
print(f"\n  ── Symbol Breakdown {'─'*49}")
print(f"  {'Symbol':<12} {'Trades':>6}  {'Wins':>5}  {'Losses':>6}  {'WR%':>5}  {'PnL':>9}")
print("  " + "-"*48)
for sym, st in sorted(symbol_trades.items()):
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {sym:<12} {ct:>6}  {st['wins']:>5}  {st['losses']:>6}  "
          f"{wr:>5.1f}  {st['pnl']:>+9.4f}")

# ── Per-regime breakdown ──────────────────────────────────────────────────────
print(f"\n  ── Regime Breakdown {'─'*49}")
print(f"  {'Regime':<22} {'Trades':>6}  {'Wins':>5}  {'Losses':>6}  {'WR%':>5}  {'PnL':>9}")
print("  " + "-"*55)
for reg, st in sorted(regime_trades.items(), key=lambda x: -(x[1]["wins"]+x[1]["losses"])):
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {reg:<22} {ct:>6}  {st['wins']:>5}  {st['losses']:>6}  "
          f"{wr:>5.1f}  {st['pnl']:>+9.4f}")

# ── Open positions at EOD ─────────────────────────────────────────────────────
if s["open_positions"]:
    print(f"\n  ── Open at End of Day ({len(s['open_positions'])} positions) {'─'*36}")
    for p in s["open_positions"]:
        print(f"  [{p['strategy']:<16}] {p['side'].upper():<5} {p['symbol']:<10}  "
              f"entry={p['entry_price']:>10,.2f}  cur={p['current_price']:>10,.2f}  "
              f"uPnL={p['unrealized_pnl']:>+8.4f}  regime={p.get('regime_label','?')}")

# ── Equity curve (sparkline) ──────────────────────────────────────────────────
if len(equity_curve) > 1:
    lo = min(equity_curve)
    hi = max(equity_curve)
    rng = hi - lo if hi != lo else 1.0
    bars = " ▁▂▃▄▅▆▇█"
    spark = "".join(bars[min(8, int((v - lo) / rng * 8))] for v in equity_curve[::4])
    print(f"\n  Equity curve (hourly):  {spark}")
    print(f"  Low: ${lo:,.2f}   High: ${hi:,.2f}")

print(f"\n  Signal journal  : {_SIM_JOURNAL}")
# Quick journal summary
if _SIM_JOURNAL.exists():
    lines = _SIM_JOURNAL.read_text().splitlines()
    events: dict = defaultdict(int)
    for l in lines:
        try:
            events[json.loads(l)["event"]] += 1
        except Exception:
            pass
    print(f"  Journal entries : {len(lines)} total  |  " +
          "  ".join(f"{k}={v}" for k, v in sorted(events.items())))

print("\n" + "="*70 + "\n")
