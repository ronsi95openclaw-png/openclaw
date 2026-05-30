"""Pre-fetch 4h candles for the 4-symbol basket from Crypto.com Exchange v1.

Endpoint: https://api.crypto.com/exchange/v1/public/get-candlestick
Public endpoint — no auth required.

Target: ~400 days (~2400 4h candles) per symbol. The public endpoint caps
results via a "count" param (we try a few values and accept whatever the
API actually returns). It does NOT reliably support end_ts pagination on
the public endpoint, so we take the largest single-call response and call
that the historical span.

Output: data/backtest/{symbol}_4h_1y.json -- list of candle dicts
(chronological, oldest first).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://api.crypto.com/exchange/v1/public/get-candlestick"
SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]
DEFAULT_TIMEFRAME = "4h"   # override via `python infra/fetch_historical_candles.py 1d`
# Try the most generous counts first; fall back if the API rejects/caps.
COUNT_LADDER = [5000, 1000, 300]
TARGET_DAYS = 400  # informational target; API may cap us lower

# Hard-learned: the public endpoint caps at 300 candles per call regardless of
# requested count, and end_ts pagination is not supported. So the only way to
# get more calendar coverage is to use a coarser timeframe (1d -> ~300 days).

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "backtest"


def fetch_candles(symbol: str, interval: str, count: int) -> dict:
    """Single GET against the public candlestick endpoint."""
    params = {
        "instrument_name": symbol,
        "timeframe": interval,
        "count": str(count),
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-prefetch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def normalize_candles(raw_data: list) -> list[dict]:
    """Coerce candle records to a stable dict shape and sort oldest-first."""
    out = []
    for c in raw_data:
        # API returns either {t, o, h, l, c, v} or already-named keys.
        t = c.get("t") if isinstance(c, dict) else None
        if t is None:
            continue
        out.append(
            {
                "t": int(t),
                "o": float(c.get("o", 0)),
                "h": float(c.get("h", 0)),
                "l": float(c.get("l", 0)),
                "c": float(c.get("c", 0)),
                "v": float(c.get("v", 0)),
            }
        )
    out.sort(key=lambda x: x["t"])
    return out


def best_effort_fetch_tf(symbol: str, timeframe: str) -> list[dict]:
    """Try each count value in the ladder; keep the largest valid result."""
    best: list[dict] = []
    last_err: Exception | None = None
    for count in COUNT_LADDER:
        try:
            payload = fetch_candles(symbol, timeframe, count)
            result = payload.get("result") or {}
            data = result.get("data") or []
            candles = normalize_candles(data)
            print(
                f"  tried count={count}: API returned {len(candles)} candles "
                f"(code={payload.get('code')})"
            )
            if len(candles) > len(best):
                best = candles
            # If we already hit a healthy result, no need to keep retrying smaller.
            if len(candles) >= 1000:
                break
        except Exception as e:  # noqa: BLE001 — any net/parse error, try next
            last_err = e
            print(f"  count={count} failed: {type(e).__name__}: {e}")
        time.sleep(0.4)
    if not best and last_err is not None:
        raise last_err
    return best


def days_span(candles: list[dict]) -> int:
    if len(candles) < 2:
        return 0
    ms = candles[-1]["t"] - candles[0]["t"]
    return int(ms // (1000 * 60 * 60 * 24))


def main() -> int:
    timeframe = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TIMEFRAME
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {OUT_DIR}")
    print(f"Timeframe:  {timeframe}")
    print(f"Target: ~{TARGET_DAYS} days of {timeframe} candles per symbol\n")

    per_symbol_summary = []
    failures = 0

    for i, symbol in enumerate(SYMBOLS):
        print(f"[{i+1}/{len(SYMBOLS)}] {symbol}")
        try:
            candles = best_effort_fetch_tf(symbol, timeframe)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {type(e).__name__}: {e}")
            failures += 1
            per_symbol_summary.append((symbol, 0, 0, None))
            continue

        span = days_span(candles)
        out_path = OUT_DIR / f"{symbol}_{timeframe}_1y.json"
        out_path.write_text(json.dumps(candles, indent=2), encoding="utf-8")
        print(
            f"  Fetched {len(candles)} candles spanning {span} days for {symbol} "
            f"-> {out_path.name}"
        )
        per_symbol_summary.append((symbol, len(candles), span, str(out_path)))

        if i < len(SYMBOLS) - 1:
            time.sleep(0.7)  # be nice to the public endpoint

    print("\n=== Summary ===")
    for sym, n, span, path in per_symbol_summary:
        status = "OK" if path else "FAIL"
        print(f"  {status:4s} {sym:10s} candles={n:>5d}  span={span:>4d}d")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
