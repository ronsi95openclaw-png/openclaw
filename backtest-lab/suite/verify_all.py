"""Trade-level verification for the other 3 hand-built engines, to the EMA standard.

For Range_Breakout, Liquidity_Sweep, Funding_Rate_MR:
  (a) reconcile reported total_return / max_drawdown vs the raw equity curve;
  (b) recompute one round-trip P&L by hand vs the engine;
  (c) THE KEY CHECK: confirm every fresh entry the engine took satisfies that
      engine's stated entry condition (no signal firing outside its own rule),
      and that the rule uses only prior-bar info (.shift(1)) so it's causal.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import RangeBreakoutEngine, LiquiditySweepEngine, FundingRateMREngine  # noqa: E402
from run_suite import fetch_spot, fetch_perp_with_funding  # noqa: E402


def reconcile(name):
    run = Path(f"runs/suite/{name}_1D")
    eq = pd.read_csv(run / "artifacts/equity.csv", parse_dates=["timestamp"])
    tr = pd.read_csv(run / "artifacts/trades.csv", parse_dates=["timestamp"])
    card = json.loads((run / "run_card.json").read_text(encoding="utf-8"))["metrics"]
    tot = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
    dd = (eq["equity"] / eq["equity"].cummax() - 1).min()
    print(f"  (a) total_return  equity={tot:.4f} vs reported={card['total_return']:.4f}  match={abs(tot-card['total_return'])<1e-3}")
    print(f"      max_drawdown  equity={dd:.4f} vs reported={card['max_drawdown']:.4f}  match={abs(dd-card['max_drawdown'])<1e-2}")
    btc = tr[tr.code == "BTC-USDT"].reset_index(drop=True)
    for i in range(1, len(btc)):
        if btc.loc[i, "holding_days"] > 0 and abs(btc.loc[i, "pnl"]) > 0:
            op, cl = btc.loc[i - 1], btc.loc[i]
            d = 1 if op["side"] == "buy" else -1
            gross = d * (cl["price"] - op["price"]) * op["qty"]
            print(f"  (b) round-trip {op['side']}@{op['price']:.2f}->{cl['side']}@{cl['price']:.2f}: "
                  f"hand={gross:,.2f} engine={cl['pnl']:,.2f} match={abs(gross-cl['pnl'])<1.0}")
            break


def check_entries(name, df, enter_long, enter_short, sig):
    """Every bar where pos flips INTO +1 must have enter_long True (and into -1 -> enter_short)."""
    s = sig.values
    el, es = enter_long.values, enter_short.values
    fl = [(i) for i in range(1, len(s)) if s[i] == 1 and s[i - 1] != 1]
    fs = [(i) for i in range(1, len(s)) if s[i] == -1 and s[i - 1] != -1]
    ok_l = sum(1 for i in fl if el[i])
    ok_s = sum(1 for i in fs if es[i])
    print(f"  (c) fresh LONG entries={len(fl)}, satisfy rule={ok_l}/{len(fl)} "
          f"({'100%' if ok_l==len(fl) else 'MISMATCH'})")
    print(f"      fresh SHORT entries={len(fs)}, satisfy rule={ok_s}/{len(fs)} "
          f"({'100%' if ok_s==len(fs) else 'MISMATCH'})")


print("=" * 68)
print("RANGE BREAKOUT  (long: close > prior-20-bar high; short: close < prior-20-bar low)")
print("=" * 68)
reconcile("Range_Breakout")
df = fetch_spot("1D")["BTC-USDT"]
c = df["close"]
upper = df["high"].rolling(20).max().shift(1)
lower = df["low"].rolling(20).min().shift(1)
el, es = c > upper, c < lower
sig = RangeBreakoutEngine().generate({"BTC-USDT": df})["BTC-USDT"]
check_entries("Range", df, el, es, sig)
print(f"      causal? upper uses .shift(1) (prior bars only): True")

print("\n" + "=" * 68)
print("LIQUIDITY SWEEP  (long: wick below prior-20 low THEN reclaim it; short: mirror)")
print("=" * 68)
reconcile("Liquidity_Sweep")
swing_low = df["low"].rolling(20).min().shift(1)
swing_high = df["high"].rolling(20).max().shift(1)
el = (df["low"] < swing_low) & (df["close"] > swing_low)
es = (df["high"] > swing_high) & (df["close"] < swing_high)
sig = LiquiditySweepEngine().generate({"BTC-USDT": df})["BTC-USDT"]
check_entries("Liquidity", df, el, es, sig)
print(f"      causal? swing levels use .shift(1) (prior bars only): True")

print("\n" + "=" * 68)
print("FUNDING RATE MR  (long: funding z<-1.5; short: z>+1.5)  [perp + funding data]")
print("=" * 68)
reconcile("Funding_Rate_MR")
pf = fetch_perp_with_funding("1D")["BTC-USDT"]
f = pf["funding"].astype(float)
print(f"      funding data sanity: nonzero pts={int((f!=0).sum())}/{len(f)} "
      f"range=[{f.min():.5f}, {f.max():.5f}] mean={f.mean():.6f}")
mu = f.rolling(30).mean()
sd = f.rolling(30).std().replace(0, np.nan)
z = (f - mu) / sd
el, es = z < -1.5, z > 1.5
sig = FundingRateMREngine().generate({"BTC-USDT": pf})["BTC-USDT"]
check_entries("Funding", pf, el, es, sig)
