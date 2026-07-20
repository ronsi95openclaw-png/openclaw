#!/usr/bin/env python3
"""
data_loader.py — Live bar feed for runner.py
=============================================
Implements build_bars_by_tf(source=None) per the runner contract (§7).

Fetches intraday OHLCV bars from yfinance for the configured instrument
and resamples them into the multi-timeframe dict the strategy needs:
  {"1m": df, "5m": df, "15m": df, "1h": df}

Each DataFrame is indexed by a tz-aware ET DatetimeIndex with lowercase
columns [open, high, low, close, volume] — exactly the format strategy.py
and the backtest engines expect.

Instrument → yfinance ticker mapping:
  ES  → ES=F   (E-mini S&P 500 futures)
  MES → MES=F  (Micro E-mini S&P 500)
  NQ  → NQ=F   (E-mini Nasdaq-100)
  MNQ → MNQ=F  (Micro E-mini Nasdaq-100)

For crypto (BTC, ETH, AVAX, SOL): pass the Liquid symbol and set
VIBE_INSTRUMENT env var to e.g. "BTC-USD" — yfinance uses that directly.

Usage (standalone test):
  python data_loader.py
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# yfinance ticker map for futures
_TICKER_MAP = {
    "ES":  "ES=F",
    "MES": "MES=F",
    "NQ":  "NQ=F",
    "MNQ": "MNQ=F",
    # crypto pass-through (yfinance symbols)
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "AVAX": "AVAX-USD",
}

_DEFAULT_INSTRUMENT = os.environ.get("VIBE_INSTRUMENT", "ES")


def _yf_ticker(instrument: str) -> str:
    return _TICKER_MAP.get(instrument.upper(), instrument)


def _build_from_csv(csv_path: str) -> Optional[dict]:
    """Parse NinjaTrader or yfinance CSV into bars_by_tf.

    NinjaTrader: Date,Time,Open,High,Low,Close,Volume (8-digit date + 6-digit time, no header tz)
    yfinance:    Datetime,open,high,low,close,volume  (offset-aware ISO timestamp as index)
    """
    try:
        import pandas as pd
        raw = pd.read_csv(csv_path, dtype=str)
        raw.columns = [c.strip() for c in raw.columns]
        first_col = raw.columns[0].lower()

        if first_col in ("datetime", "date time", "timestamp"):
            # yfinance format — first column is an offset-aware ISO datetime
            raw = raw.rename(columns={raw.columns[0]: "_dt"})
            raw["_dt"] = pd.to_datetime(raw["_dt"], utc=True).dt.tz_convert(ET)
            df = raw.set_index("_dt")
            df.columns = [c.lower() for c in df.columns]
        else:
            # NinjaTrader format — Date (YYYYMMDD) + Time (HHMMSS) columns
            df = raw.copy()
            df["_dt"] = pd.to_datetime(
                df["Date"].str.zfill(8) + df["Time"].str.zfill(6),
                format="%Y%m%d%H%M%S",
            )
            df = df.set_index("_dt")
            df.index = df.index.tz_localize(ET)
            df.columns = [c.lower() for c in df.columns]

        df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        if df.empty:
            return None
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        return {
            "1m":  df,
            "5m":  df.resample("5min").agg(agg).dropna(subset=["open", "close"]),
            "15m": df.resample("15min").agg(agg).dropna(subset=["open", "close"]),
            "1h":  df.resample("1h").agg(agg).dropna(subset=["open", "close"]),
        }
    except Exception:
        return None


def build_bars_by_tf(source=None) -> Optional[dict]:
    """Fetch live bars and return bars_by_tf or None on failure.

    ``source`` is ignored when None (live fetch). When a CSV path is passed
    the file is parsed directly (NinjaTrader format: Date,Time,Open,High,Low,Close,Volume).
    """
    if source is not None:
        return _build_from_csv(str(source))

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return None

    instrument = _DEFAULT_INSTRUMENT
    ticker = _yf_ticker(instrument)

    try:
        # Pull 5 days of 1m bars (max yfinance allows for 1m is 7d)
        raw_1m = yf.download(
            ticker,
            period="5d",
            interval="1m",
            progress=False,
            auto_adjust=True,
        )
        if raw_1m is None or raw_1m.empty:
            return None
    except Exception:
        return None

    # Flatten MultiIndex columns if present (yfinance ≥0.2 returns them)
    if isinstance(raw_1m.columns, pd.MultiIndex):
        raw_1m.columns = raw_1m.columns.get_level_values(0)

    raw_1m.columns = [c.lower() for c in raw_1m.columns]
    if not all(c in raw_1m.columns for c in ("open", "high", "low", "close")):
        return None

    # Ensure ET-aware index
    if raw_1m.index.tz is None:
        raw_1m.index = raw_1m.index.tz_localize("UTC").tz_convert(ET)
    else:
        raw_1m.index = raw_1m.index.tz_convert(ET)

    # Drop NaN rows and add volume col if missing
    raw_1m = raw_1m[["open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )
    if "volume" not in raw_1m.columns:
        raw_1m["volume"] = 0

    if raw_1m.empty:
        return None

    # Resample up to coarser timeframes
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    try:
        df_5m  = raw_1m.resample("5min").agg(agg).dropna(subset=["open", "close"])
        df_15m = raw_1m.resample("15min").agg(agg).dropna(subset=["open", "close"])
        df_1h  = raw_1m.resample("1h").agg(agg).dropna(subset=["open", "close"])
    except Exception:
        return None

    out = {
        "1m":  raw_1m,
        "5m":  df_5m,
        "15m": df_15m,
        "1h":  df_1h,
    }
    # Drop empty frames
    return {k: v for k, v in out.items() if not v.empty} or None


if __name__ == "__main__":
    print(f"Fetching live bars for {_DEFAULT_INSTRUMENT} ({_yf_ticker(_DEFAULT_INSTRUMENT)})…")
    result = build_bars_by_tf()
    if result is None:
        print("ERROR: no bars returned (check yfinance install or market hours)")
    else:
        for tf, df in result.items():
            print(f"  {tf}: {len(df)} bars  last={df.index[-1]}  close={df['close'].iloc[-1]:.2f}")
