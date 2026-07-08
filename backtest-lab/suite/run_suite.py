"""Full backtest suite: 5 strategies x {BTC,ETH,SOL} x {1D,4H} on OKX data,
through vibe-trading's own CryptoEngine. Aggregate metrics per strategy, ranked.

Usage:  python suite/run_suite.py [1D] [4H]   (default: both)
Run from the backtest-lab dir with PYTHONUTF8=1 and
VIBE_TRADING_ALLOWED_RUN_ROOTS=<cwd>/runs set.
"""
import json
import sys
import time
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from backtest.loaders.okx import DataLoader as OKXLoader
from backtest.engines.crypto import CryptoEngine
from backtest.metrics import calc_bars_per_year

sys.path.insert(0, str(Path(__file__).resolve().parent))
from strategies import ENGINES  # noqa: E402

SPOT_CODES = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
PERP_SYMS = {"BTC-USDT": "BTC/USDT:USDT", "ETH-USDT": "ETH/USDT:USDT", "SOL-USDT": "SOL/USDT:USDT"}
START, END = "2022-06-13", "2026-06-13"
METRIC_KEYS = ["sharpe", "win_rate", "max_drawdown", "total_return", "trade_count",
               "annual_return", "sortino", "calmar", "profit_factor", "benchmark_return"]


class PreloadedLoader:
    def __init__(self, dm):
        self._d = dm

    def fetch(self, *a, **k):
        return self._d


SPOT_SYMS = {"BTC-USDT": "BTC/USDT", "ETH-USDT": "ETH/USDT", "SOL-USDT": "SOL/USDT"}


def fetch_spot(interval):
    # Use ccxt (not the tool's OKX loader) because that loader caps ~1439 bars
    # regardless of interval — fine for daily (4yr) but only ~8mo for 4H.
    # ccxt paginates fully -> consistent 4yr on both timeframes.
    ex = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    tf = "1d" if interval == "1D" else "4h"
    start_ms = int(pd.Timestamp(START).timestamp() * 1000)
    end_ms = int(pd.Timestamp(END).timestamp() * 1000)
    return {code: _paginate_ohlcv(ex, sym, tf, start_ms, end_ms) for code, sym in SPOT_SYMS.items()}


def _paginate_ohlcv(ex, sym, tf, start_ms, end_ms):
    rows, since = [], start_ms
    while since < end_ms:
        b = ex.fetch_ohlcv(sym, tf, since=since, limit=300)
        if not b:
            break
        rows += b
        since = b[-1][0] + 1
        if len(b) < 300:
            break
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df = df[(df["ts"] >= start_ms) & (df["ts"] <= end_ms)]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


def _paginate_funding(ex, sym, start_ms, end_ms):
    rows, since = [], start_ms
    while since < end_ms:
        try:
            b = ex.fetch_funding_rate_history(sym, since=since, limit=100)
        except Exception as e:
            print(f"    funding fetch error {sym}: {e}")
            break
        if not b:
            break
        rows += b
        since = b[-1]["timestamp"] + 1
        if len(b) < 100:
            break
        time.sleep(0.12)
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.to_datetime(r["timestamp"], unit="ms"): float(r["fundingRate"]) for r in rows})
    return s.sort_index()


def fetch_perp_with_funding(interval):
    ex = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    tf = "1d" if interval == "1D" else "4h"
    start_ms = int(pd.Timestamp(START).timestamp() * 1000)
    end_ms = int(pd.Timestamp(END).timestamp() * 1000)
    dm = {}
    for code, sym in PERP_SYMS.items():
        df = _paginate_ohlcv(ex, sym, tf, start_ms, end_ms)
        funding = _paginate_funding(ex, sym, start_ms, end_ms)
        if len(funding):
            df["funding"] = funding.reindex(df.index, method="ffill")
            df["funding"] = df["funding"].bfill().fillna(0.0)
        else:
            df["funding"] = 0.0
        dm[code] = df
    return dm


def run_strategy(name, EngineCls, data_map, interval):
    config = {"codes": list(data_map.keys()), "start_date": START, "end_date": END,
              "source": "okx", "interval": interval, "engine": "daily"}
    bpy = calc_bars_per_year(interval, "okx")
    run_dir = Path(f"runs/suite/{name}_{interval}")
    run_dir.mkdir(parents=True, exist_ok=True)
    eng = CryptoEngine(config)
    m = eng.run_backtest(config, PreloadedLoader(data_map), EngineCls(), run_dir, bars_per_year=bpy)
    if not isinstance(m, dict):
        rc = run_dir / "run_card.json"
        m = json.loads(rc.read_text(encoding="utf-8")).get("metrics", {}) if rc.exists() else {}
    return m


def main():
    intervals = [a for a in sys.argv[1:]] or ["1D", "4H"]
    results = []
    for interval in intervals:
        print(f"\n{'='*70}\nINTERVAL {interval}\n{'='*70}")
        spot = fetch_spot(interval)
        for c, d in spot.items():
            print(f"  spot {c}: {len(d)} bars {d.index[0].date()}->{d.index[-1].date()}")
        perp = fetch_perp_with_funding(interval)
        for c, d in perp.items():
            fnd = d['funding'].abs().gt(0).sum()
            print(f"  perp {c}: {len(d)} bars {d.index[0].date()}->{d.index[-1].date()}, funding pts={fnd}")
        for name, Cls in ENGINES.items():
            dm = perp if name == "Funding_Rate_MR" else spot
            try:
                m = run_strategy(name, Cls, dm, interval)
                row = {"strategy": name, "interval": interval}
                row.update({k: m.get(k) for k in METRIC_KEYS})
                print(f"  [OK] {name:18s} sharpe={m.get('sharpe')!s:8.8} "
                      f"ret={m.get('total_return')!s:8.8} dd={m.get('max_drawdown')!s:8.8} "
                      f"win={m.get('win_rate')!s:6.6} n={m.get('trade_count')}")
            except Exception as e:
                row = {"strategy": name, "interval": interval, "error": str(e)[:120]}
                print(f"  [FAIL] {name}: {e}")
            results.append(row)

    # ----- ranked output -----
    out = ["# Backtest Suite Results", "", f"OKX data, {START} -> {END}. Aggregate across BTC/ETH/SOL.",
           "Ranked by Sharpe within each timeframe.", ""]
    for interval in intervals:
        rows = [r for r in results if r["interval"] == interval and "error" not in r]
        rows.sort(key=lambda r: (r.get("sharpe") if r.get("sharpe") is not None else -999), reverse=True)
        out.append(f"## {interval}\n")
        out.append("| Rank | Strategy | Sharpe | Total Ret | Max DD | Win Rate | Trades | Annual | Sortino |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            def f(x, p=2):
                return "n/a" if x is None else (f"{x:.{p}f}")
            out.append(f"| {i} | {r['strategy']} | {f(r.get('sharpe'))} | "
                       f"{f(r.get('total_return'))} | {f(r.get('max_drawdown'))} | "
                       f"{f(r.get('win_rate'))} | {r.get('trade_count')} | "
                       f"{f(r.get('annual_return'))} | {f(r.get('sortino'))} |")
        errs = [r for r in results if r["interval"] == interval and "error" in r]
        for r in errs:
            out.append(f"| - | {r['strategy']} | ERROR: {r['error']} |")
        out.append("")
    report = "\n".join(out)
    Path("suite/RESULTS.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    Path("suite/results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
