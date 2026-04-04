"""
Crypto.com Exchange — public candle data fetcher.
No API key needed for market data (public endpoints only).
"""
from __future__ import annotations

import logging
import requests

logger = logging.getLogger("clawbot.trading.exchange")

_BASE = "https://api.crypto.com/exchange/v1/public"

_TIMEFRAME_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",
    "6h":  "6h",
    "12h": "12h",
    "1d":  "1D",
    "1w":  "1W",
}


def fetch_closes(instrument: str, timeframe: str = "4h", count: int = 100) -> list[float]:
    """
    Fetch closing prices from Crypto.com public candlestick API.

    Args:
        instrument: Crypto.com pair, e.g. "BTC_USDT"
        timeframe:  "1h", "4h", "1d" etc.
        count:      Number of candles (max 300)

    Returns:
        List of close prices as floats, oldest first.
    """
    tf  = _TIMEFRAME_MAP.get(timeframe, timeframe)
    url = f"{_BASE}/get-candlestick"
    params = {"instrument_name": instrument, "timeframe": tf, "count": count}

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Crypto.com API error: {payload.get('message', payload)}")

    candles = payload.get("result", {}).get("data", [])
    if not candles:
        raise ValueError(f"No candle data returned for {instrument}/{timeframe}")

    closes = [float(c["c"]) for c in candles]
    logger.info(f"Fetched {len(closes)} {timeframe} candles for {instrument}")
    return closes


def fetch_all_closes(coins: list[str], timeframe: str = "4h", count: int = 100) -> dict:
    """
    Fetch closes for multiple coins. Skips any that fail.

    Returns:
        {"BTC_USDT": [float, ...], ...}
    """
    result: dict = {}
    for coin in coins:
        try:
            result[coin] = fetch_closes(coin, timeframe, count)
        except Exception as exc:
            logger.warning(f"Skipping {coin}: {exc}")
    return result


def fetch_ticker_price(instrument: str) -> float:
    """Fetch the latest trade price for a single instrument."""
    url    = f"{_BASE}/get-ticker"
    params = {"instrument_name": instrument}
    r      = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    payload = r.json()
    return float(payload["result"]["data"][0]["a"])  # ask price
