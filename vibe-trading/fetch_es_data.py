#!/usr/bin/env python3
"""
fetch_es_data.py — Free live ES futures data via yfinance (no broker/account needed).

Downloads ES=F 5-minute bars (15-min delayed, free) and writes to
backtest/data/ES_5M_live.csv. runner.py already parses yfinance CSV format.

Usage:
    python fetch_es_data.py            # last 5 days of 5m bars
    python fetch_es_data.py --days 7   # last 7 days (max 60)
    python fetch_es_data.py --out path/to/custom.csv

Then run bot:
    python bot/runner.py --csv backtest/data/ES_5M_live.csv --once
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DEFAULT_OUT = _HERE / "backtest" / "data" / "ES_5M_live.csv"
_TICKER = "ES=F"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch free ES futures 5m bars via yfinance")
    parser.add_argument("--days", type=int, default=5,
                        help="Days of history (1-60; yfinance 5m cap is 60 days)")
    parser.add_argument("--out", default=None, help="Output CSV path (default: backtest/data/ES_5M_live.csv)")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("ERROR: yfinance not installed.")
        print("  pip install yfinance        (in the Claude-openclaw .venv)")
        return 1

    days = min(max(1, args.days), 60)
    out = Path(args.out) if args.out else _DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {_TICKER} 5m bars (last {days}d) ...")
    try:
        ticker = yf.Ticker(_TICKER)
        df = ticker.history(period=f"{days}d", interval="5m", auto_adjust=True)
    except Exception as exc:
        print(f"ERROR fetching data: {exc}")
        return 1

    if df is None or df.empty:
        print("No data returned. Possible causes:")
        print("  - Market closed + weekend (try Monday)")
        print("  - yfinance rate-limited (wait 1 min, retry)")
        print("  - No internet connection")
        return 1

    # Normalize columns to lowercase (runner.py _parse_csv_to_df expects lowercase)
    df = df.rename(columns={c: c.lower() for c in df.columns})
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep]

    df.to_csv(out, index=True, index_label="Datetime")

    bars = len(df)
    last_ts = df.index[-1] if bars > 0 else "N/A"
    print(f"OK: {bars} bars -> {out}")
    print(f"Last bar: {last_ts}")
    print()
    print("Run bot (paper mode):")
    print(f"  python bot/runner.py --csv {out} --once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
