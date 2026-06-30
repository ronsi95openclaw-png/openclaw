"""Independent correctness checks on the EMA_Trend_Retest_1D run:
reconcile reported metrics against the raw trade/equity artifacts, recompute one
trade's P&L by hand, and confirm the signal engine fires where its logic says."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import EMATrendRetestEngine  # noqa: E402
from run_suite import fetch_spot  # noqa: E402

run = Path("runs/suite/EMA_Trend_Retest_1D")
tr = pd.read_csv(run / "artifacts/trades.csv", parse_dates=["timestamp"])
eq = pd.read_csv(run / "artifacts/equity.csv", parse_dates=["timestamp"])
card = json.loads((run / "run_card.json").read_text(encoding="utf-8"))["metrics"]

print("=== 1. total_return: equity curve vs reported metric ===")
tot_eq = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
print(f"  from equity.csv : {tot_eq:.4f}")
print(f"  reported metric : {card['total_return']:.4f}   -> match={abs(tot_eq-card['total_return'])<1e-3}")

print("\n=== 2. max_drawdown: equity curve vs reported ===")
roll_max = eq["equity"].cummax()
dd = (eq["equity"] / roll_max - 1).min()
print(f"  from equity.csv : {dd:.4f}")
print(f"  reported metric : {card['max_drawdown']:.4f}   -> match={abs(dd-card['max_drawdown'])<1e-2}")

print("\n=== 3. sum(trade pnl) vs equity change ($1M start) ===")
realized = tr["pnl"].sum()
eq_change = eq["equity"].iloc[-1] - eq["equity"].iloc[0]
print(f"  sum(trades.pnl) : {realized:,.0f}")
print(f"  equity change   : {eq_change:,.0f}   (diff = open position at end, if any)")

print("\n=== 4. recompute ONE BTC round-trip P&L by hand vs engine ===")
btc = tr[tr.code == "BTC-USDT"].reset_index(drop=True)
for i in range(1, len(btc)):
    if btc.loc[i, "holding_days"] > 0 and abs(btc.loc[i, "pnl"]) > 0:
        op, cl = btc.loc[i - 1], btc.loc[i]
        d = 1 if op["side"] == "buy" else -1
        gross = d * (cl["price"] - op["price"]) * op["qty"]
        print(f"  open {op['side']}@{op['price']:.2f} qty={op['qty']:.4f}  ->  close {cl['side']}@{cl['price']:.2f}")
        print(f"  hand gross pnl  = {d}*({cl['price']:.2f}-{op['price']:.2f})*{op['qty']:.4f} = {gross:,.2f}")
        print(f"  engine net pnl  = {cl['pnl']:,.2f}   (difference = ~17bps fees+slippage, expected)")
        print(f"  plausible       = {abs(gross - cl['pnl']) < abs(gross)*0.03 + 50}")
        break

print("\n=== 5. EMA signal logic actually fires on uptrend+pullback? ===")
dm = fetch_spot("1D")
df = dm["BTC-USDT"]
sig = EMATrendRetestEngine().generate({"BTC-USDT": df})["BTC-USDT"]
c = df["close"]
ef = c.ewm(span=20, adjust=False).mean()
es = c.ewm(span=50, adjust=False).mean()
print(f"  signal counts: {sig.value_counts().to_dict()}")
chg = sig.diff().fillna(0)
new_longs = df.index[(sig == 1) & (chg != 0)][:4]
print("  sample fresh LONG entries (expect uptrend ema20>ema50 AND pullback low<=ema20):")
for ts in new_longs:
    ok = (ef[ts] > es[ts]) and (df["low"][ts] <= ef[ts]) and (c[ts] > ef[ts])
    print(f"    {ts.date()}: close={c[ts]:.0f} ema20={ef[ts]:.0f} ema50={es[ts]:.0f} "
          f"up={ef[ts]>es[ts]} pullback={df['low'][ts]<=ef[ts]} -> logic_holds={ok}")
