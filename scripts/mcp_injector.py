#!/usr/bin/env python3
"""MCP data injector — writes fresh Crypto.com candle/ticker data to the bridge
file cache so the bot uses real prices even without a direct MCP connection.

Run this script from a Claude session after fetching MCP data, or call
inject_live_data() with the raw MCP responses directly.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# Normalise path so we can import bridge utilities
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading.cryptocom_mcp_bridge import _normalize_mcp_candles, _normalize_mcp_ticker

CACHE_DIR = Path(__file__).parent.parent / "data" / "mcp_cache"


def write_candles(symbol: str, raw_mcp_response: Any) -> int:
    """Normalise and cache MCP candle response for one symbol. Returns count."""
    candles = _normalize_mcp_candles(raw_mcp_response)
    if not candles:
        print(f"[injector] WARNING: no candles parsed for {symbol}")
        return 0
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}_candles.json"
    path.write_text(json.dumps({"written_at": time.time(), "candles": candles}, indent=2))
    print(f"[injector] {symbol} candles: {len(candles)} written → {path.name}")
    return len(candles)


def write_ticker(symbol: str, raw_mcp_response: Any) -> bool:
    """Normalise and cache MCP ticker response for one symbol."""
    ticker = _normalize_mcp_ticker(raw_mcp_response)
    if not ticker or not ticker.get("last"):
        print(f"[injector] WARNING: no ticker parsed for {symbol}")
        return False
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}_ticker.json"
    path.write_text(json.dumps({"written_at": time.time(), "ticker": ticker}, indent=2))
    print(f"[injector] {symbol} ticker: last={ticker['last']:.4f} → {path.name}")
    return True


def inject_live_data(
    btc_candles: Any, eth_candles: Any, sol_candles: Any,
    btc_ticker: Any,  eth_ticker: Any,  sol_ticker: Any,
) -> None:
    """Inject all six MCP responses at once. Called from Claude sessions."""
    write_candles("BTC_USDT", btc_candles)
    write_candles("ETH_USDT", eth_candles)
    write_candles("SOL_USDT", sol_candles)
    write_ticker("BTC_USDT",  btc_ticker)
    write_ticker("ETH_USDT",  eth_ticker)
    write_ticker("SOL_USDT",  sol_ticker)
    print(f"[injector] All data written at {time.strftime('%H:%M:%S UTC', time.gmtime())}")


def show_cache_status() -> None:
    """Print age and last-close for each cached symbol."""
    now = time.time()
    for sym in ("BTC_USDT", "ETH_USDT", "SOL_USDT"):
        cp = CACHE_DIR / f"{sym}_candles.json"
        tp = CACHE_DIR / f"{sym}_ticker.json"
        if cp.exists():
            d = json.loads(cp.read_text())
            age = int(now - d["written_at"])
            last_c = d["candles"][-1]["close"] if d["candles"] else "?"
            print(f"  {sym} candles: {len(d['candles'])} bars  last_close={last_c}  age={age}s")
        else:
            print(f"  {sym} candles: NOT CACHED")
        if tp.exists():
            d = json.loads(tp.read_text())
            age = int(now - d["written_at"])
            print(f"  {sym} ticker : last={d['ticker'].get('last','?')}  age={age}s")
        else:
            print(f"  {sym} ticker : NOT CACHED")


if __name__ == "__main__":
    show_cache_status()
