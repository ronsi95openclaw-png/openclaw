"""
paper_watch_liquiditysweep.py — Log LiquiditySweep signals against live Crypto.com
data WITHOUT executing any trades.

Designed to run once per day (1d candles only update once per day; running more
frequently produces duplicate evaluations). The companion Windows scheduled task
`ClawBot-LiquiditySweep-Watch` invokes this via `infra/paper_watch_run.bat`.

Appends one JSONL entry per (symbol, run) to:
  data/paper_watch/liquidity_sweep.jsonl

NEVER places an order. NEVER touches the executor. Pure observation.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

# Make Unicode safe on Windows cp1252 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Make the script invocation-agnostic: works from any cwd, as a script or a module.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trading.strategies.liquidity_sweep import LiquiditySweepStrategy  # noqa: E402

# Match the proven v1 endpoint used by infra/fetch_historical_candles.py.
API_URL = "https://api.crypto.com/exchange/v1/public/get-candlestick"
TIMEFRAME = "1d"
CANDLE_COUNT = 100  # >> warmup (which is ~40 for LiquiditySweep with defaults)

SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]
OUTPUT = REPO_ROOT / "data" / "paper_watch" / "liquidity_sweep.jsonl"


def fetch_recent_candles(symbol: str) -> list[dict] | None:
    """Public endpoint — no auth required. Returns chronological-order candles or None."""
    url = f"{API_URL}?instrument_name={symbol}&timeframe={TIMEFRAME}&count={CANDLE_COUNT}"
    req = urllib.request.Request(url, headers={"User-Agent": "openclaw-paperwatch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return None
    if payload.get("code") != 0:
        return None
    raw = (payload.get("result") or {}).get("data") or []
    # API may return newest-first; sort to chronological.
    raw.sort(key=lambda c: c.get("t", 0))
    return raw


def evaluate_one(symbol: str, strategy: LiquiditySweepStrategy) -> dict:
    """Fetch candles, run the strategy, return a serializable observation."""
    base = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe": TIMEFRAME,
    }
    candles = fetch_recent_candles(symbol)
    if candles is None:
        return {**base, "error": "fetch failed"}
    closes = [float(c["c"]) for c in candles]
    if len(closes) < strategy.warmup:
        return {**base, "error": f"need {strategy.warmup} closes, got {len(closes)}"}

    try:
        signal = strategy.evaluate(symbol, closes)
    except Exception as e:  # noqa: BLE001
        return {**base, "error": f"strategy raised {type(e).__name__}: {e}"}

    if is_dataclass(signal):
        signal_payload = asdict(signal)
    else:
        signal_payload = getattr(signal, "__dict__", {"repr": repr(signal)})

    return {
        **base,
        "last_close": closes[-1],
        "n_closes": len(closes),
        "signal": signal_payload,
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    strategy = LiquiditySweepStrategy()

    print(f"paper-watch run @ {datetime.now(timezone.utc).isoformat()}")
    print(f"output: {OUTPUT}")
    with OUTPUT.open("a", encoding="utf-8") as f:
        for symbol in SYMBOLS:
            result = evaluate_one(symbol, strategy)
            f.write(json.dumps(result) + "\n")
            if "error" in result:
                print(f"  {symbol}: ERROR {result['error']}")
            else:
                sig = result["signal"]
                print(
                    f"  {symbol}: {sig.get('action'):<4} "
                    f"conf={sig.get('confidence'):<6} "
                    f"close={result['last_close']:.4f} "
                    f"rsi={sig.get('rsi', 0):.1f}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
