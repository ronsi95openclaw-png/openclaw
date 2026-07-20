#!/usr/bin/env python3
"""
fetch_4yr_data.py — Download 4-year ES/NQ data via yfinance
============================================================
Downloads:
  • ES (ES=F) — 4 years daily, 2 years 1H
  • NQ (NQ=F) — 4 years daily, 2 years 1H

yfinance interval limits:
  • 1d  — unlimited history
  • 1h  — max ~730 days (2 years)
  • 5m  — max 60 days (use NinjaTrader CSV for 5M production runs)

Output: data/ES_1D.csv, data/ES_1H.csv, data/NQ_1D.csv, data/NQ_1H.csv
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("ERROR: yfinance/pandas not installed.")
    print("  Run: pip install yfinance pandas numpy tqdm")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

TICKERS = {
    "ES": "ES=F",    # E-mini S&P 500 continuous front-month
    "NQ": "NQ=F",    # E-mini Nasdaq-100 continuous front-month
}

FETCH_CONFIGS = [
    {"timeframe": "1d", "interval": "1d", "days_back": 365 * 4 + 30},   # 4+ years
    {"timeframe": "1h", "interval": "1h", "days_back": 729},              # 729d — yf hard limit is 730 days
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_one(instrument: str, ticker: str, interval: str, days_back: int) -> Path | None:
    end = datetime.now()
    start = end - timedelta(days=days_back)

    label = f"{instrument} {interval} ({days_back // 365:.0f}yr)"
    print(f"  Fetching {label} ...", end=" ", flush=True)

    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        print(f"FAILED — {e}")
        return None

    if df is None or df.empty:
        print("FAILED — no data returned (check network / ticker validity)")
        return None

    # Flatten MultiIndex columns that yfinance sometimes returns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Ensure standard column names
    df = df.rename(columns=str.capitalize)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = 0

    df = df[needed].dropna(subset=["Open", "High", "Low", "Close"])

    # Convert index to UTC-aware → string for portability
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York")
    df.index.name = "Datetime"

    tf_tag = interval.upper().replace("D", "D").replace("H", "H")
    outfile = DATA_DIR / f"{instrument}_{tf_tag}.csv"
    df.to_csv(outfile)

    first = df.index[0] if len(df) > 0 else "?"
    last  = df.index[-1] if len(df) > 0 else "?"
    print(f"{len(df):,} bars  [{str(first)[:10]} → {str(last)[:10]}]  → {outfile.name}")
    return outfile


def main() -> dict[str, Path]:
    print("=" * 65)
    print("  4-Year Data Fetch  (yfinance)")
    print("=" * 65)

    fetched: dict[str, Path] = {}
    errors: list[str] = []

    for instr, ticker in TICKERS.items():
        print(f"\n[{instr}]  ticker={ticker}")
        for cfg in FETCH_CONFIGS:
            key = f"{instr}_{cfg['timeframe'].upper()}"
            path = fetch_one(instr, ticker, cfg["interval"], cfg["days_back"])
            if path:
                fetched[key] = path
            else:
                errors.append(key)

    print("\n" + "─" * 65)
    print(f"  ✅ {len(fetched)} datasets ready   ❌ {len(errors)} failed")
    if errors:
        print(f"  Failed: {', '.join(errors)}")
    print("─" * 65)

    return fetched


if __name__ == "__main__":
    main()
