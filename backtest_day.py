"""Realistic backtest using real Crypto.com 15-min candle data + block-bootstrap extension.

Real data: 50 candles per symbol from Crypto.com MCP (saved to data/historical/).
Extension:
  - Block bootstrap: samples 8-candle blocks to preserve local momentum/autocorrelation.
  - Trend injection: after every ~5 blocks, 25% chance to inject a 25-50 candle synthetic
    trend (bull run or bear drop), creating the trending conditions that EMA_CROSS,
    BREAKOUT, and TREND_FOLLOW need.
SL/TP:  checked against candle high/low (OHLC-aware, not random walk).
        LONG:  SL if candle.low <= sl_price  |  TP if candle.high >= tp_price
        SHORT: SL if candle.high >= sl_price |  TP if candle.low  <= tp_price

Usage:
    python backtest_day.py [--cycles N] [--balance F] [--goal F]
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

HISTORICAL_DIR  = Path(__file__).parent / "data" / "historical"
CANDLE_INTERVAL = 900   # 15 min in seconds

parser = argparse.ArgumentParser()
parser.add_argument("--cycles",  type=int,   default=96,     help="Simulation cycles (default 96 = 24 h)")
parser.add_argument("--balance", type=float, default=100.0,  help="Starting balance USDT (default 100)")
parser.add_argument("--goal",    type=float, default=20000.0,help="Profit goal USDT (default 20000)")
parser.add_argument("--analyse", action="store_true",        help="Run Claude Opus analysis after backtest")
args = parser.parse_args()

CYCLES       = args.cycles
LOOKBACK     = 100
TOTAL_NEEDED = LOOKBACK + CYCLES
STARTING_BAL = args.balance
GOAL         = args.goal


# ── Block Bootstrap with Trend Injection ─────────────────────────────────────

def _bootstrap_extend(real_candles: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Extend candles using block bootstrap + periodic trend injection.

    Block bootstrap (block size 8) preserves momentum autocorrelation within
    blocks. Trend injection inserts 25-50 candle directional moves every ~5
    blocks, creating the trending conditions that EMA/breakout strategies need.
    """
    rng        = random.Random(seed)
    BLOCK      = 8       # consecutive-return block size
    TREND_PROB = 0.40    # probability per inter-block gap of injecting a trend
    TREND_MIN  = 20      # min candles per injected trend
    TREND_MAX  = 45      # max candles per injected trend
    DRIFT_MIN  = 0.05    # per-candle drift %
    DRIFT_MAX  = 0.22

    closes  = [c["close"]  for c in real_candles]
    returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    uw      = [(c["high"] - max(c["open"], c["close"])) / c["close"] for c in real_candles]
    lw      = [(min(c["open"], c["close"]) - c["low"])  / c["close"] for c in real_candles]
    vols    = [c["volume"] for c in real_candles]

    def _make_candle(open_p: float, ret: float, ts: int) -> dict:
        cls = open_p * math.exp(ret)
        body_hi = max(open_p, cls)
        body_lo = min(open_p, cls)
        hi  = body_hi * (1.0 + abs(rng.choice(uw)))
        lo  = body_lo * (1.0 - abs(rng.choice(lw)))
        return {
            "ts":     ts,
            "open":   round(open_p, 6),
            "high":   round(hi,     6),
            "low":    round(max(1e-6, lo), 6),
            "close":  round(cls,    6),
            "volume": round(rng.choice(vols), 4),
        }

    result   = []
    last_ts  = real_candles[-1]["ts"]
    last_cls = real_candles[-1]["close"]
    blocks_since_trend = 0

    max_block_start = max(0, len(returns) - BLOCK)

    while len(result) < n:
        # ── Maybe inject a trend ──────────────────────────────────────────────
        blocks_since_trend += 1
        if blocks_since_trend >= 3 and rng.random() < TREND_PROB:
            blocks_since_trend = 0
            trend_len  = rng.randint(TREND_MIN, TREND_MAX)
            direction  = rng.choice([1, -1])  # +1 = bull, -1 = bear
            drift      = rng.uniform(DRIFT_MIN, DRIFT_MAX) / 100  # per candle
            sigma      = rng.uniform(0.03, 0.08) / 100            # noise around drift
            for _ in range(trend_len):
                if len(result) >= n:
                    break
                r = direction * drift + rng.gauss(0, sigma)
                last_ts += CANDLE_INTERVAL
                c = _make_candle(last_cls, r, last_ts)
                result.append(c)
                last_cls = c["close"]

        if len(result) >= n:
            break

        # ── Block bootstrap (8 consecutive returns) ───────────────────────────
        if max_block_start > 0:
            start  = rng.randint(0, max_block_start)
            block  = returns[start : start + BLOCK]
        else:
            block  = [rng.choice(returns) for _ in range(BLOCK)]

        for r in block:
            if len(result) >= n:
                break
            last_ts += CANDLE_INTERVAL
            c = _make_candle(last_cls, r, last_ts)
            result.append(c)
            last_cls = c["close"]

    return result[:n]


# ── Load & extend real candles ────────────────────────────────────────────────

SYMBOLS_MAP = {
    "BTC-USDT": "BTC-USDT_15m.json",
    "ETH-USDT": "ETH-USDT_15m.json",
    "SOL-USDT": "SOL-USDT_15m.json",
}

all_candles: dict[str, list[dict]] = {}
for sym, fname in SYMBOLS_MAP.items():
    path  = HISTORICAL_DIR / fname
    real  = json.loads(path.read_text())
    extra = max(0, TOTAL_NEEDED - len(real))
    boot  = _bootstrap_extend(real, extra + 200, seed=hash(sym) % 10000)
    all_candles[sym] = real + boot
    print(f"  {sym}: {len(real)} real + {len(boot)} bootstrap = {len(all_candles[sym])} candles")


# ── Patch BloFinBot for backtest ──────────────────────────────────────────────

from trading.sim_engine import BloFinBot, _STATE_FILE, _OUTCOMES_FILE, LEVERAGE
from trading.strategies import _WEIGHTS_FILE, STRATEGIES
import trading.sim_engine as _bot_mod

_JOURNAL_TS   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
_SIM_JOURNAL  = _bot_mod._JOURNAL_FILE.parent / f"signal_journal_bt_{_JOURNAL_TS}.jsonl"
_SIM_OUTCOMES = _bot_mod._OUTCOMES_FILE.parent / f"trade_outcomes_bt_{_JOURNAL_TS}.jsonl"
_bot_mod._JOURNAL_FILE  = _SIM_JOURNAL
_bot_mod._OUTCOMES_FILE = _SIM_OUTCOMES


def _reset():
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({
        "demo_mode": True, "risk_pct": 1.5,
        "total_pnl": 0.0, "trades_date": "", "trades_today": 0, "trade_log": [],
    }))
    _WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WEIGHTS_FILE.write_text(json.dumps({
        s: {"trades": 0, "wins": 0, "losses": 0, "weight": 1.0}
        for s in STRATEGIES
    }))


_reset()


class BacktestBot(BloFinBot):
    """BloFinBot subclass that drives simulated OHLC candle data."""

    def __init__(self, candle_data: dict[str, list[dict]]):
        self._candle_data = candle_data
        self._cursor      = LOOKBACK
        super().__init__()

    def _fake_candles(self, symbol: str) -> list[dict]:
        data = self._candle_data.get(symbol, [])
        return data[self._cursor - LOOKBACK : self._cursor]

    def _current_price(self, pos: dict) -> float:
        data = self._candle_data.get(pos["symbol"], [])
        idx  = min(self._cursor, len(data) - 1)
        return float(data[idx]["close"])

    def _check_positions(self) -> None:
        """OHLC-aware SL/TP — SL and TP checked against candle high and low."""
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
                pos["unrealized_pnl"] = round(
                    pnl_pct * pos["entry_price"] * pos["size"] * LEVERAGE, 4)
                if lo <= pos["sl_price"]:
                    to_close.append((pos, "loss", pos["sl_price"]))
                elif hi >= pos["tp_price"]:
                    to_close.append((pos, "win", pos["tp_price"]))
            else:
                pnl_pct = -(cls - pos["entry_price"]) / pos["entry_price"]
                pos["unrealized_pnl"] = round(
                    pnl_pct * pos["entry_price"] * pos["size"] * LEVERAGE, 4)
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
print("  BloFin Bot — Backtest (Block Bootstrap + Trend Injection)")
print(f"  Symbols: BTC / ETH / SOL  |  Interval: 15m  |  Cycles: {CYCLES} ({CYCLES//4}h)")
print(f"  Starting balance: ${STARTING_BAL:,.2f}  |  Risk: 1.5%  |  Leverage: 3×")
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
        strategy_trades[t["strategy"]][k]     += 1
        strategy_trades[t["strategy"]]["pnl"] += t["pnl"]
        symbol_trades[t["symbol"]][k]         += 1
        symbol_trades[t["symbol"]]["pnl"]     += t["pnl"]
        r = t.get("regime_label", "UNKNOWN")
        regime_trades[r][k]                   += 1
        regime_trades[r]["pnl"]               += t["pnl"]
        all_closed.append(t)
    prev_closed_cnt = len(new_closed)

    if cycle % 4 == 0 or cycle == CYCLES:
        hour         = cycle // 4
        total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
        print(f"  {hour:>4}h  {cycle:>5}  ${equity:>9,.2f}  "
              f"{s['total_pnl']:>+9.2f}  {total_closed:>6}  "
              f"{len(s['open_positions']):>4}  {s['status_msg'][:35]}")

elapsed = time.time() - start_time

# ── Final report ──────────────────────────────────────────────────────────────
s            = bot.get_status()
final_eq     = s["balance"] + s["unrealized_pnl"]
ret_pct      = (final_eq - STARTING_BAL) / STARTING_BAL * 100
total_closed = sum(v["wins"] + v["losses"] for v in strategy_trades.values())
total_wins   = sum(v["wins"]              for v in strategy_trades.values())
total_losses = sum(v["losses"]            for v in strategy_trades.values())
overall_wr   = total_wins / total_closed * 100 if total_closed else 0.0

print("\n" + "="*70)
print("  BACKTEST RESULTS — Block Bootstrap + Trend Injection")
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

if _SIM_OUTCOMES.exists():
    outcome_lines = _SIM_OUTCOMES.read_text().splitlines()
    print(f"\n  Trade outcomes log: {_SIM_OUTCOMES.name}  ({len(outcome_lines)} records)")
    # Print last 3 outcomes as a sample of WHY analysis
    if outcome_lines:
        print("  Sample narratives:")
        for ln in outcome_lines[-3:]:
            try:
                r = json.loads(ln)
                label = "WIN " if r["outcome"] == "win" else "LOSS"
                print(f"    [{label}] {r['narrative'][:120]}")
            except Exception:
                pass

# ── $100 → goal projector ─────────────────────────────────────────────────────
if total_closed >= 2 and total_wins > 0:
    exp_per_trade_pct = (exp / STARTING_BAL) * 100
    trades_per_day    = total_closed / (CYCLES / 96) if CYCLES >= 96 else total_closed
    daily_growth_pct  = exp_per_trade_pct * trades_per_day if trades_per_day > 0 else 0.0

    print(f"\n  ── $100 → ${GOAL:,.0f} Projector {'─'*38}")
    print(f"  Expectancy per trade : {exp_per_trade_pct:+.3f}% of account")
    print(f"  Avg trades / day     : {trades_per_day:.2f}")
    print(f"  Projected daily gain : {daily_growth_pct:+.3f}%")
    if daily_growth_pct > 0:
        days = math.log(GOAL / STARTING_BAL) / math.log(1 + daily_growth_pct / 100)
        print(f"  Days to ${GOAL:,.0f}       : ~{days:.0f} days ({days/30:.1f} months)")
        print(f"  Milestones (compounding):")
        for m in [500, 1000, 2500, 5000, 10000, 20000]:
            if m > STARTING_BAL:
                d = math.log(m / STARTING_BAL) / math.log(1 + daily_growth_pct / 100)
                print(f"    ${m:>6,.0f}  →  ~{d:.0f} days ({d/30:.1f} mo)")
    else:
        print("  (Negative expectancy — strategies need more tuning)")

print("\n" + "="*70 + "\n")

# ── Claude Opus analysis (--analyse flag or auto when ANTHROPIC_API_KEY set) ──
_should_analyse = args.analyse or (
    bool(__import__("os").getenv("ANTHROPIC_API_KEY")) and total_closed >= 5
)
if _should_analyse:
    try:
        from runtime.claude_analyst import run_analysis
        run_analysis(outcomes_file=_SIM_OUTCOMES if _SIM_OUTCOMES.exists() else None)
    except Exception as _ae:
        print(f"  [Claude Analyst] Skipped — {_ae}")
elif total_closed >= 5:
    print("  Tip: run with --analyse (or set ANTHROPIC_API_KEY) to get Claude Opus strategy recommendations\n")
