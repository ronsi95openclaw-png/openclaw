"""Realistic backtest using real Crypto.com 15-min candle data + bootstrap extension.

Real data: 50 candles per symbol fetched live from Crypto.com MCP.
Extension: bootstrap resampling from real return distribution to reach 7 days.
SL/TP:     checked against candle high/low — not a random walk.
           For a LONG: SL triggered if candle.low <= sl_price
                       TP triggered if candle.high >= tp_price
           For a SHORT: SL triggered if candle.high >= sl_price
                        TP triggered if candle.low <= tp_price

Usage:
    python backtest_day.py [--cycles N]   # default 96 (24 h)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING, stream=sys.stdout)

HISTORICAL_DIR = Path(__file__).parent / "data" / "historical"
CANDLE_INTERVAL = 900   # 15 min in seconds

parser = argparse.ArgumentParser()
parser.add_argument("--cycles", type=int, default=96, help="Simulation cycles (default 96 = 24 h)")
args = parser.parse_args()

CYCLES       = args.cycles
LOOKBACK     = 100       # candles needed for indicators
TOTAL_NEEDED = LOOKBACK + CYCLES
STARTING_BAL = 1000.0

# ── Bootstrap engine ──────────────────────────────────────────────────────────

def _bootstrap_extend(real_candles: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Extend a real candle series by n candles using bootstrap resampling.

    Samples log-returns and intra-candle OHLC shapes from the empirical
    distribution of real candles. Preserves actual volatility, fat tails,
    and wick structure rather than using Gaussian noise.
    """
    rng = random.Random(seed)
    closes  = [c["close"] for c in real_candles]
    # Log returns
    returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    # Upper wick: (high - max(open,close)) / close
    uw = [(c["high"] - max(c["open"], c["close"])) / c["close"] for c in real_candles]
    # Lower wick: (min(open,close) - low) / close
    lw = [(min(c["open"], c["close"]) - c["low"])  / c["close"] for c in real_candles]
    # Volume samples
    vols = [c["volume"] for c in real_candles]

    result   = []
    last_ts  = real_candles[-1]["ts"]
    last_cls = real_candles[-1]["close"]

    for i in range(n):
        r   = rng.choice(returns)
        new_close = last_cls * math.exp(r)
        new_open  = last_cls                         # gap-free

        body_high = max(new_open, new_close)
        body_low  = min(new_open, new_close)
        new_high  = body_high * (1 + rng.choice(uw))
        new_low   = body_low  * (1 - rng.choice(lw))
        new_vol   = rng.choice(vols)
        new_ts    = last_ts + CANDLE_INTERVAL * (i + 1)

        result.append({
            "ts":     new_ts,
            "open":   round(new_open,  6),
            "high":   round(new_high,  6),
            "low":    round(max(0.0001, new_low), 6),
            "close":  round(new_close, 6),
            "volume": round(new_vol,   4),
        })
        last_cls = new_close

    return result


# ── Load & extend real candles ────────────────────────────────────────────────

SYMBOLS_MAP = {
    "BTC-USDT": "BTC-USDT_15m.json",
    "ETH-USDT": "ETH-USDT_15m.json",
    "SOL-USDT": "SOL-USDT_15m.json",
}

all_candles: dict[str, list[dict]] = {}
for sym, fname in SYMBOLS_MAP.items():
    path = HISTORICAL_DIR / fname
    real = json.loads(path.read_text())
    extra_needed = max(0, TOTAL_NEEDED - len(real))
    bootstrap    = _bootstrap_extend(real, extra_needed + 200, seed=hash(sym) % 10000)
    all_candles[sym] = real + bootstrap
    print(f"  {sym}: {len(real)} real + {len(bootstrap)} bootstrap = {len(all_candles[sym])} candles")

# ── Patch BloFinBot for backtest ──────────────────────────────────────────────

from trading.blofin_bot import BloFinBot, _STATE_FILE, LEVERAGE
from trading.blofin_strategies import _WEIGHTS_FILE
import trading.blofin_bot as _bot_mod

_JOURNAL_TS  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
_SIM_JOURNAL = _bot_mod._JOURNAL_FILE.parent / f"signal_journal_bt_{_JOURNAL_TS}.jsonl"
_bot_mod._JOURNAL_FILE = _SIM_JOURNAL

def _reset():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({
        "demo_mode": True, "risk_pct": 1.5,
        "total_pnl": 0.0, "trades_date": "", "trades_today": 0, "trade_log": [],
    }))
    _WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WEIGHTS_FILE.write_text(json.dumps({
        s: {"trades": 0, "wins": 0, "losses": 0, "weight": 1.0}
        for s in ["EMA_CROSS", "RSI_MEAN_REVERT", "BREAKOUT", "FUNDING_ARB"]
    }))

_reset()


class BacktestBot(BloFinBot):
    """BloFinBot subclass that replaces random price walks with real OHLC data."""

    def __init__(self, candle_data: dict[str, list[dict]]):
        self._candle_data = candle_data
        self._cursor      = LOOKBACK   # start at index LOOKBACK so we have a full window
        super().__init__()

    def _fake_candles(self, symbol: str) -> list[dict]:
        data = self._candle_data.get(symbol, [])
        return data[self._cursor - LOOKBACK : self._cursor]

    def _current_price(self, pos: dict) -> float:
        data = self._candle_data.get(pos["symbol"], [])
        idx  = min(self._cursor, len(data) - 1)
        return float(data[idx]["close"])

    def _check_positions(self) -> None:
        """OHLC-aware SL/TP check — more realistic than a random price walk."""
        to_close: list[tuple] = []

        for pos in list(self.state.open_positions):
            data = self._candle_data.get(pos["symbol"], [])
            idx  = min(self._cursor, len(data) - 1)
            c    = data[idx]

            hi  = float(c["high"])
            lo  = float(c["low"])
            cls = float(c["close"])
            pos["current_price"] = cls

            if pos["side"] == "long":
                pnl_pct = (cls - pos["entry_price"]) / pos["entry_price"]
                pos["unrealized_pnl"] = round(pnl_pct * pos["entry_price"] * pos["size"] * LEVERAGE, 4)
                if lo <= pos["sl_price"]:
                    to_close.append((pos, "loss", pos["sl_price"]))
                elif hi >= pos["tp_price"]:
                    to_close.append((pos, "win", pos["tp_price"]))
            else:
                pnl_pct = -(cls - pos["entry_price"]) / pos["entry_price"]
                pos["unrealized_pnl"] = round(pnl_pct * pos["entry_price"] * pos["size"] * LEVERAGE, 4)
                if hi >= pos["sl_price"]:
                    to_close.append((pos, "loss", pos["sl_price"]))
                elif lo <= pos["tp_price"]:
                    to_close.append((pos, "win", pos["tp_price"]))

        for pos, outcome, exit_price in to_close:
            self._close_position(pos, outcome, exit_price)

    def advance(self):
        self._cursor += 1


bot = BacktestBot(all_candles)
bot.configure(demo_mode=True, risk_pct=1.5)
bot.state.balance = STARTING_BAL

# ── Tracking ──────────────────────────────────────────────────────────────────
strategy_trades = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
symbol_trades   = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
regime_trades   = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
equity_curve    = [STARTING_BAL]
all_closed      = []
peak_equity     = STARTING_BAL
max_drawdown    = 0.0
prev_closed_cnt = 0
start_time      = time.time()

print("\n" + "="*70)
print("  BloFin Bot — Backtest on Real Crypto.com Data + Bootstrap Extension")
print(f"  Symbols: BTC / ETH / SOL  |  Interval: 15m  |  Cycles: {CYCLES} ({CYCLES//4}h)")
print(f"  Starting balance: ${STARTING_BAL:,.2f}  |  Risk: 1.5%  |  Leverage: 3×")
print(f"  SL/TP: checked against real candle high/low")
print("="*70)
print(f"  {'Hour':>4}  {'Cycle':>5}  {'Equity':>10}  {'PnL':>10}  {'Trades':>6}  {'Open':>4}  {'Status'}")
print("  " + "-"*68)

for cycle in range(1, CYCLES + 1):
    bot._scan()
    bot.advance()

    s      = bot.get_status()
    equity = s["balance"] + s["unrealized_pnl"]
    equity_curve.append(equity)

    if equity > peak_equity:
        peak_equity = equity
    dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
    if dd > max_drawdown:
        max_drawdown = dd

    new_closed = s["trade_log"]
    for t in new_closed[:max(0, len(new_closed) - prev_closed_cnt)]:
        k = "wins" if t["outcome"] == "win" else "losses"
        strategy_trades[t["strategy"]][k]   += 1
        strategy_trades[t["strategy"]]["pnl"] += t["pnl"]
        symbol_trades[t["symbol"]][k]         += 1
        symbol_trades[t["symbol"]]["pnl"]     += t["pnl"]
        r = t.get("regime_label", "UNKNOWN")
        regime_trades[r][k]                   += 1
        regime_trades[r]["pnl"]               += t["pnl"]
        all_closed.append(t)
    prev_closed_cnt = len(new_closed)

    if cycle % 4 == 0 or cycle == CYCLES:
        hour = cycle // 4
        total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
        print(f"  {hour:>4}h  {cycle:>5}  ${equity:>9,.2f}  "
              f"{s['total_pnl']:>+9.2f}  {total_closed:>6}  "
              f"{len(s['open_positions']):>4}  {s['status_msg'][:35]}")

elapsed = time.time() - start_time

# ── Final report ──────────────────────────────────────────────────────────────
s           = bot.get_status()
final_eq    = s["balance"] + s["unrealized_pnl"]
ret_pct     = (final_eq - STARTING_BAL) / STARTING_BAL * 100
total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
total_wins   = sum(v["wins"]              for v in strategy_trades.values())
total_losses = sum(v["losses"]            for v in strategy_trades.values())
overall_wr   = total_wins / total_closed * 100 if total_closed else 0.0

print("\n" + "="*70)
print("  BACKTEST RESULTS — Real Crypto.com Data")
print("="*70)
print(f"\n  Period      : {CYCLES} cycles × 15 min = {CYCLES // 4} hours")
print(f"  Wall-clock  : {elapsed:.1f}s")
print(f"\n  Start       : ${STARTING_BAL:,.2f}")
print(f"  Final equity: ${final_eq:,.2f}  ({ret_pct:+.2f}%)")
print(f"  Realised PnL: {s['total_pnl']:+.4f} USDT")
print(f"  Unrealised  : {s['unrealized_pnl']:+.4f} USDT")
print(f"  Peak equity : ${peak_equity:,.2f}")
print(f"  Max drawdown: {max_drawdown:.2f}%")

print(f"\n  Trades      : {total_closed}  |  Wins: {total_wins}  |  Losses: {total_losses}  "
      f"|  WR: {overall_wr:.1f}%")

if total_closed:
    avg_win  = sum(t["pnl"] for t in all_closed if t["outcome"] == "win")  / max(total_wins,  1)
    avg_loss = sum(t["pnl"] for t in all_closed if t["outcome"] == "loss") / max(total_losses, 1)
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    exp      = (overall_wr / 100 * avg_win) + ((1 - overall_wr / 100) * avg_loss)
    print(f"  Avg win     : {avg_win:+.4f}   Avg loss: {avg_loss:+.4f}   R:R {rr:.2f}×")
    print(f"  Expectancy  : {exp:+.4f} USDT / trade")

print(f"\n  ── Strategy Breakdown {'─'*47}")
print(f"  {'Strategy':<18} {'Trades':>6}  {'Wins':>5}  {'Loss':>5}  {'WR%':>5}  {'PnL':>10}  {'Weight':>6}")
print("  " + "-"*62)
for name, sw in s["strategy_weights"].items():
    st = strategy_trades[name]
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {name:<18} {ct:>6}  {st['wins']:>5}  {st['losses']:>5}  "
          f"{wr:>5.1f}  {st['pnl']:>+10.4f}  {sw['weight']:>6.2f}")

print(f"\n  ── Symbol Breakdown {'─'*49}")
print(f"  {'Symbol':<12} {'Trades':>6}  {'Wins':>5}  {'Loss':>5}  {'WR%':>5}  {'PnL':>10}")
print("  " + "-"*50)
for sym, st in sorted(symbol_trades.items()):
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {sym:<12} {ct:>6}  {st['wins']:>5}  {st['losses']:>5}  {wr:>5.1f}  {st['pnl']:>+10.4f}")

print(f"\n  ── Regime Breakdown {'─'*49}")
print(f"  {'Regime':<22} {'Trades':>6}  {'Wins':>5}  {'Loss':>5}  {'WR%':>5}  {'PnL':>10}")
print("  " + "-"*56)
for reg, st in sorted(regime_trades.items(), key=lambda x: -(x[1]["wins"]+x[1]["losses"])):
    ct = st["wins"] + st["losses"]
    wr = st["wins"] / ct * 100 if ct else 0.0
    print(f"  {reg:<22} {ct:>6}  {st['wins']:>5}  {st['losses']:>5}  {wr:>5.1f}  {st['pnl']:>+10.4f}")

if s["open_positions"]:
    print(f"\n  ── Open Positions at EOD {'─'*44}")
    for p in s["open_positions"]:
        print(f"  [{p['strategy']:<16}] {p['side'].upper():<5} {p['symbol']:<10}  "
              f"entry={p['entry_price']:>10,.2f}  cur={p['current_price']:>10,.2f}  "
              f"uPnL={p['unrealized_pnl']:>+8.4f}  regime={p.get('regime_label','?')}")

if len(equity_curve) > 1:
    lo, hi = min(equity_curve), max(equity_curve)
    rng_v  = hi - lo if hi != lo else 1.0
    bars   = " ▁▂▃▄▅▆▇█"
    spark  = "".join(bars[min(8, int((v - lo) / rng_v * 8))] for v in equity_curve[::4])
    print(f"\n  Equity (hourly):  {spark}")
    print(f"  Low ${lo:,.2f}  →  High ${hi:,.2f}")

if _SIM_JOURNAL.exists():
    lines  = _SIM_JOURNAL.read_text().splitlines()
    events: dict = defaultdict(int)
    for ln in lines:
        try:   events[json.loads(ln)["event"]] += 1
        except Exception: pass
    print(f"\n  Signal journal: {_SIM_JOURNAL.name}")
    print(f"  {len(lines)} entries — " + "  ".join(f"{k}={v}" for k, v in sorted(events.items())))

print("\n" + "="*70 + "\n")
