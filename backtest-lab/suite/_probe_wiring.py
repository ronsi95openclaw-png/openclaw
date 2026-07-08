"""Probe: validate that ccxt-fetched data + the tool's CryptoEngine reproduces
the runner-CLI smoke result for SMC/BTC daily 2023-2024. If metrics match, the
custom harness is faithful and we build the full suite on it."""
import json
import sys
import time
from pathlib import Path

import ccxt
import pandas as pd
from smartmoneyconcepts import smc

from backtest.engines.crypto import CryptoEngine
from backtest.metrics import calc_bars_per_year


# --- SMC signal engine (verbatim logic from src/skills/smc/example_signal_engine.py) ---
class SignalEngine:
    def __init__(self, swing_length: int = 10, close_break: bool = True):
        self.swing_length = swing_length
        self.close_break = close_break

    def generate(self, data_map):
        result = {}
        for code, df in data_map.items():
            signal = pd.Series(0, index=df.index)
            ohlc = df[["open", "high", "low", "close", "volume"]].copy()
            if len(ohlc) < self.swing_length * 2:
                result[code] = signal
                continue
            try:
                swing_hl = smc.swing_highs_lows(ohlc, swing_length=self.swing_length)
                bos_choch = smc.bos_choch(ohlc, swing_highs_lows=swing_hl, close_break=self.close_break)
                fvg = smc.fvg(ohlc)
                bos_val = bos_choch["BOS"].fillna(0).astype(int)
                choch_val = bos_choch["CHOCH"].fillna(0).astype(int)
                fvg_val = fvg["FVG"].fillna(0).astype(int)
                structure = choch_val.where(choch_val != 0, bos_val)
                buy = (structure == 1) & (fvg_val >= 0)
                sell = (structure == -1) & (fvg_val <= 0)
                signal[:] = (buy.astype(int) - sell.astype(int)).values
            except Exception as e:
                print(f"  {code} SMC error: {e}")
            result[code] = signal
        return result


class PreloadedLoader:
    def __init__(self, data_map):
        self._d = data_map

    def fetch(self, codes, start_date, end_date, fields=None, interval="1D"):
        return self._d


def fetch_ohlcv(symbol, timeframe, start_ms, end_ms):
    ex = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    rows = []
    since = start_ms
    while since < end_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=300)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + 1
        if len(batch) < 300:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df = df[(df["ts"] >= start_ms) & (df["ts"] <= end_ms)]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    start_ms = int(pd.Timestamp("2023-01-01").timestamp() * 1000)
    end_ms = int(pd.Timestamp("2024-12-31").timestamp() * 1000)
    print("Fetching BTC-USDT 1D 2023-2024 from OKX via ccxt...")
    df = fetch_ohlcv("BTC/USDT", "1d", start_ms, end_ms)
    print(f"  {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}")

    data_map = {"BTC-USDT": df}
    config = {"codes": ["BTC-USDT"], "start_date": "2023-01-01", "end_date": "2024-12-31",
              "source": "okx", "interval": "1D", "engine": "daily"}
    run_dir = Path("runs/_probe_wiring")
    run_dir.mkdir(parents=True, exist_ok=True)
    bpy = calc_bars_per_year("1D", "okx")
    eng = CryptoEngine(config)
    ret = eng.run_backtest(config, PreloadedLoader(data_map), SignalEngine(), run_dir, bars_per_year=bpy)
    print("=== run_backtest return ===")
    print(json.dumps(ret, default=str)[:600] if ret else "(returned None)")
    rc = run_dir / "run_card.json"
    if rc.exists():
        card = json.loads(rc.read_text(encoding="utf-8"))
        m = card.get("metrics", card)
        print("=== run_card metrics (compare to smoke: sharpe~-2.13, win~0.118, maxDD~-0.285, trades=17) ===")
        for k in ["sharpe", "win_rate", "max_drawdown", "total_return", "trade_count"]:
            print(f"  {k}: {m.get(k)}")
