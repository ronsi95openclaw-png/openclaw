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
from datetime import datetime, timezone

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts_to_str(ms: int) -> str:
    """Convert millisecond timestamp to readable UTC string."""
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_keys() -> tuple[str, str]:
    api_key = os.getenv("CRYPTOCOM_API_KEY", "").strip()
    secret  = os.getenv("CRYPTOCOM_SECRET", "").strip()
    if not api_key or not secret:
        raise EnvironmentError("CRYPTOCOM_API_KEY and CRYPTOCOM_SECRET must be set in .env")
    return api_key, secret


def _sign(method: str, params: dict, api_key: str, secret: str) -> dict:
    """
    Build a signed request body for Crypto.com Exchange API v2.

    Signature spec:
        sig_payload = method + str(id) + api_key + param_string + str(nonce)
        param_string = alphabetically sorted keys, each key immediately followed
                       by its value, no separators between pairs
        signature = HMAC-SHA256(secret_key_utf8, sig_payload_utf8).hexdigest()

    id and nonce are sent as integers in the JSON body.
    """
    nonce  = int(time.time() * 1000)
    req_id = nonce

    # Deterministic param string: sorted keys, values cast to str, no separators
    param_str = "".join(f"{k}{params[k]}" for k in sorted(params))
    sig_payload = f"{method}{req_id}{api_key}{param_str}{nonce}"
    signature   = hmac.new(
        secret.encode("utf-8"),
        sig_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        "id":      req_id,   # integer per API spec
        "method":  method,
        "api_key": api_key,
        "params":  params,
        "nonce":   nonce,    # integer per API spec
        "sig":     signature,
    }


def _check_response(payload: dict) -> None:
    """Raise a descriptive ValueError for non-zero API response codes."""
    code = payload.get("code", 0)
    if code != 0:
        msg = payload.get("message", "unknown error")
        # Common codes:
        #   10002 UNAUTHORIZED  — bad signature or key revoked / IP-restricted
        #   10003 INVALID_NONCE — nonce too old or replayed
        #   10007 INVALID_REQUEST_BODY — malformed request
        hints = {
            10002: " (check key is active, not IP-restricted, and secret is correct)",
            10003: " (nonce too old — check system clock sync)",
            10004: " (endpoint not found — key may lack Trading permissions or endpoint name changed)",
            10007: " (malformed request body)",
        }
        raise ValueError(
            f"Crypto.com API error {code}: {msg}{hints.get(code, '')}"
        )


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
def _post_private(endpoint: str, body: dict) -> dict:
    """
    POST to a private endpoint. Raises a descriptive ValueError on API errors
    (including auth failures) rather than a raw HTTP 401 exception. Retried
    on transient failure by @with_backoff (covers all 3 private-endpoint
    call sites below, not just get_account_balance).
    """
    r = requests.post(f"{_PRIVATE}/{endpoint}", json=body, timeout=10)
    # Try to read the JSON payload first so we can give a meaningful error
    try:
        payload = r.json()
    except Exception:
        r.raise_for_status()
        raise
    _check_response(payload)   # raises ValueError with code/message on non-zero codes
    r.raise_for_status()       # catches any non-JSON HTTP errors (5xx etc.)
    return payload


def get_account_balance() -> dict:
    """
    Fetch account balances from Crypto.com.
    Returns: {"BTC": {"available": float, "total": float}, ...}
    """
    api_key, secret = _get_keys()
    body    = _sign("private/get-account-summary", {}, api_key, secret)
    payload = _post_private("get-account-summary", body)

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


def get_trade_history(instrument: str = None, page_size: int = 20) -> list:
    """
    Fetch recent trade history from Crypto.com private API.
    Returns list of trade dicts: {timestamp, instrument, side, price, qty, fee, trade_id}
    """
    api_key, secret = _get_keys()
    params: dict = {"page_size": page_size}
    if instrument:
        params["instrument_name"] = instrument
    body    = _sign("private/get-trades", params, api_key, secret)
    payload = _post_private("get-trades", body)
    trades = payload.get("result", {}).get("trade_list", [])
    result = []
    for t in trades:
        result.append({
            "trade_id":     t.get("trade_id", ""),
            "instrument":   t.get("instrument_name", ""),
            "side":         t.get("side", ""),          # BUY or SELL
            "price":        float(t.get("traded_price", 0)),
            "qty":          float(t.get("traded_quantity", 0)),
            "fee":          float(t.get("fee", 0)),
            "fee_currency": t.get("fee_currency", ""),
            "timestamp":    t.get("create_time", 0),
            "ts_str":       _ts_to_str(t.get("create_time", 0)),
        })
    return result


def get_open_orders(instrument: str = None) -> list:
    """Fetch currently open orders from Crypto.com."""
    api_key, secret = _get_keys()
    params: dict = {}
    if instrument:
        params["instrument_name"] = instrument
    body    = _sign("private/get-open-orders", params, api_key, secret)
    payload = _post_private("get-open-orders", body)
    orders = payload.get("result", {}).get("order_list", [])
    result = []
    for o in orders:
        result.append({
            "order_id":   o.get("order_id", ""),
            "instrument": o.get("instrument_name", ""),
            "side":       o.get("side", ""),
            "type":       o.get("type", ""),
            "price":      float(o.get("price", 0)),
            "qty":        float(o.get("quantity", 0)),
            "filled_qty": float(o.get("cumulative_quantity", 0)),
            "status":     o.get("status", ""),
            "timestamp":  o.get("create_time", 0),
            "ts_str":     _ts_to_str(o.get("create_time", 0)),
        })
    return result
