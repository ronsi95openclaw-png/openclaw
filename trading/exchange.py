"""
Crypto.com Exchange connector.
- Public endpoints: candle data, ticker prices (no auth needed)
- Private endpoints: account balance, order placement (HMAC-SHA256 signed)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

import requests

from trading.backoff import with_backoff

logger = logging.getLogger("clawbot.trading.exchange")

_PUBLIC  = "https://api.crypto.com/v2/public"
_PRIVATE = "https://api.crypto.com/v2/private"

_TIMEFRAME_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1D", "1w": "1W",
}


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_keys() -> tuple[str, str]:
    api_key = os.getenv("CRYPTOCOM_API_KEY", "").strip()
    secret  = os.getenv("CRYPTOCOM_SECRET", "").strip()
    if not api_key or not secret:
        raise EnvironmentError("CRYPTOCOM_API_KEY and CRYPTOCOM_SECRET must be set in .env")
    return api_key, secret


def _sign(method: str, params: dict, api_key: str, secret: str) -> dict:
    """Build a signed request body for Crypto.com private API."""
    nonce   = str(int(time.time() * 1000))
    req_id  = nonce

    # Deterministic param string: sorted keys, no spaces
    param_str = "".join(f"{k}{params[k]}" for k in sorted(params))
    sig_payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    signature   = hmac.new(secret.encode(), sig_payload.encode(), hashlib.sha256).hexdigest()

    return {
        "id":      req_id,
        "method":  method,
        "api_key": api_key,
        "params":  params,
        "nonce":   nonce,
        "sig":     signature,
    }


# ── Public endpoints ──────────────────────────────────────────────────────────

@with_backoff()
def fetch_closes(instrument: str, timeframe: str = "4h", count: int = 100) -> list[float]:
    """Closing prices from Crypto.com public candlestick API, oldest first."""
    tf  = _TIMEFRAME_MAP.get(timeframe, timeframe)
    url = f"{_PUBLIC}/get-candlestick"
    r   = requests.get(url, params={"instrument_name": instrument, "timeframe": tf, "count": count}, timeout=10)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Crypto.com error: {payload.get('message', payload)}")

    candles = payload.get("result", {}).get("data", [])
    if not candles:
        raise ValueError(f"No candle data for {instrument}/{timeframe}")

    closes = [float(c["c"]) for c in candles]
    logger.info(f"Fetched {len(closes)} {timeframe} candles for {instrument}")
    return closes


def fetch_all_closes(coins: list[str], timeframe: str = "4h", count: int = 100) -> dict:
    """Fetch closes for multiple coins. Skips any that fail."""
    result: dict = {}
    for coin in coins:
        try:
            result[coin] = fetch_closes(coin, timeframe, count)
        except Exception as exc:
            logger.warning(f"Skipping {coin}: {exc}")
    return result


@with_backoff()
def fetch_ticker_price(instrument: str) -> float:
    """Latest ask price for an instrument."""
    r = requests.get(f"{_PUBLIC}/get-ticker", params={"instrument_name": instrument}, timeout=8)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Crypto.com error: {payload.get('message', payload)}")

    data = payload.get("result", {}).get("data")
    if not data:
        raise ValueError(f"No ticker data for {instrument}")
    return float(data[0]["a"])


# ── Private endpoints ─────────────────────────────────────────────────────────

@with_backoff()
def get_account_balance() -> dict:
    """
    Fetch account balances from Crypto.com.
    Returns: {"BTC": {"available": float, "total": float}, ...}
    """
    api_key, secret = _get_keys()
    body = _sign("private/get-account-summary", {}, api_key, secret)
    r    = requests.post(f"{_PRIVATE}/get-account-summary", json=body, timeout=10)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Crypto.com error: {payload.get('message', payload)}")

    balances = {}
    for acc in payload.get("result", {}).get("accounts", []):
        currency = acc.get("currency", "")
        if acc.get("balance", 0) > 0:
            balances[currency] = {
                "available": float(acc.get("available", 0)),
                "total":     float(acc.get("balance", 0)),
            }
    return balances


def get_portfolio_value_usd(balances: dict) -> float:
    """
    Estimate total USD value of portfolio from balances.
    Uses live ticker prices for non-USDT assets.
    """
    total = 0.0
    for currency, amounts in balances.items():
        qty = amounts["total"]
        if qty <= 0:
            continue
        if currency in ("USDT", "USD"):
            total += qty
        else:
            try:
                price  = fetch_ticker_price(f"{currency}_USDT")
                total += qty * price
            except Exception as exc:
                logger.warning(f"Portfolio valuation: ticker fetch failed for {currency}_USDT, excluding from total: {exc}")
    return round(total, 2)
