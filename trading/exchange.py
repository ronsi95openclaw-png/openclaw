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
import threading
import time

import requests

logger = logging.getLogger("clawbot.trading.exchange")

_PUBLIC  = "https://api.crypto.com/exchange/v1/public"
_PRIVATE = "https://api.crypto.com/exchange/v1/private"

_TIMEFRAME_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1D", "1w": "1W",
}


# ── Nonce counter — monotonic, thread-safe, never repeats within process ─────

_nonce_lock    = threading.Lock()
_nonce_counter = 0


def _next_nonce() -> str:
    """Return a strictly-increasing nonce safe for concurrent callers."""
    global _nonce_counter
    base = int(time.time() * 1000)
    with _nonce_lock:
        # Ensure nonce is always strictly greater than the previous one
        _nonce_counter = max(base, _nonce_counter + 1)
        return str(_nonce_counter)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_keys() -> tuple[str, str]:
    api_key = os.getenv("CRYPTOCOM_API_KEY", "").strip()
    secret  = os.getenv("CRYPTOCOM_SECRET", "").strip()
    if not api_key or not secret:
        raise EnvironmentError("CRYPTOCOM_API_KEY and CRYPTOCOM_SECRET must be set in .env")
    return api_key, secret


def _sign(method: str, params: dict, api_key: str, secret: str) -> dict:
    """Build a signed request body for Crypto.com private API."""
    nonce   = _next_nonce()
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


def fetch_candles(instrument: str, timeframe: str = "15m", count: int = 100) -> list[dict]:
    """Full OHLCV candles from Crypto.com, oldest first.

    Returns list of dicts compatible with strategy and regime classifier:
      {"ts": int, "open": float, "high": float, "low": float,
       "close": float, "volume": float}
    """
    tf  = _TIMEFRAME_MAP.get(timeframe, timeframe)
    url = f"{_PUBLIC}/get-candlestick"
    r   = requests.get(url, params={"instrument_name": instrument, "timeframe": tf, "count": count}, timeout=10)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Crypto.com candles error: {payload.get('message', payload)}")

    raw = payload.get("result", {}).get("data", [])
    if not raw:
        raise ValueError(f"No candle data for {instrument}/{timeframe}")

    # API returns newest-first — reverse to oldest-first
    raw = list(reversed(raw))
    candles = [
        {
            "ts":     int(c.get("t", 0)),
            "open":   float(c.get("o", 0) or c.get("open",  0)),
            "high":   float(c.get("h", 0) or c.get("high",  0)),
            "low":    float(c.get("l", 0) or c.get("low",   0)),
            "close":  float(c.get("c", 0) or c.get("close", 0)),
            "volume": float(c.get("v", 0) or c.get("volume",0)),
        }
        for c in raw
    ]
    logger.debug("Fetched %d %s candles for %s", len(candles), timeframe, instrument)
    return candles


def fetch_ticker(instrument: str) -> dict:
    """Latest bid/ask/last for an instrument (full dict, matches BloFin format)."""
    r = requests.get(f"{_PUBLIC}/get-ticker", params={"instrument_name": instrument}, timeout=8)
    r.raise_for_status()
    raw = r.json()["result"]["data"][0]
    return {
        "last":       float(raw.get("a", raw.get("last", 0))),
        "bid":        float(raw.get("b", 0)),
        "ask":        float(raw.get("a", 0)),
        "volume_24h": float(raw.get("v", 0)),
        "change_24h": float(raw.get("c", 0) or 0),
    }


def fetch_funding_rate(instrument: str) -> float:
    """Funding rate for a perpetual. Returns 0.0 for spot instruments."""
    # Crypto.com perpetuals are e.g. BTCUSD-PERP
    if "PERP" not in instrument.upper():
        return 0.0
    try:
        url = f"{_PUBLIC}/get-valuations"
        r   = requests.get(url, params={"instrument_name": instrument,
                                        "valuation_type": "funding_rate"}, timeout=8)
        r.raise_for_status()
        data = r.json().get("result", {}).get("data", [])
        if data:
            return float(data[0].get("v", 0) or 0)
    except Exception:
        pass
    return 0.0


def fetch_all_closes(coins: list[str], timeframe: str = "4h", count: int = 100) -> dict:
    """Fetch closes for multiple coins. Skips any that fail."""
    result: dict = {}
    for coin in coins:
        try:
            result[coin] = fetch_closes(coin, timeframe, count)
        except Exception as exc:
            logger.warning(f"Skipping {coin}: {exc}")
    return result


def fetch_ticker_price(instrument: str) -> float:
    """Latest ask price for an instrument."""
    r = requests.get(f"{_PUBLIC}/get-ticker", params={"instrument_name": instrument}, timeout=8)
    r.raise_for_status()
    return float(r.json()["result"]["data"][0]["a"])


# ── Private endpoints ─────────────────────────────────────────────────────────

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
        if currency == "USDT":
            total += qty
        else:
            try:
                price  = fetch_ticker_price(f"{currency}_USDT")
                total += qty * price
            except Exception:
                pass
    return round(total, 2)


# ── Perpetual futures ─────────────────────────────────────────────────────────

# Internal symbol → Crypto.com perpetual instrument name
_PERP_INSTRUMENT = {
    "BTC_USDT": "BTCUSD-PERP",
    "ETH_USDT": "ETHUSD-PERP",
    "SOL_USDT": "SOLUSD-PERP",
}

# Minimum order quantity per perp instrument (base currency)
_MIN_QTY_PERP = {
    "BTCUSD-PERP": 0.001,
    "ETHUSD-PERP": 0.01,
    "SOLUSD-PERP": 1.0,
}


def to_perp_instrument(symbol: str) -> str:
    """Convert internal symbol (BTC_USDT) to Crypto.com perp name (BTCUSD-PERP)."""
    return _PERP_INSTRUMENT.get(symbol, symbol)


def set_leverage(instrument: str, leverage: int) -> bool:
    """Set leverage for a perpetual instrument. Returns True on success."""
    api_key, secret = _get_keys()
    params = {"instrument_name": instrument, "leverage": leverage}
    body   = _sign("private/set-leverage", params, api_key, secret)
    try:
        r = requests.post(f"{_PRIVATE}/set-leverage", json=body, timeout=10)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code", 0) != 0:
            logger.warning("set_leverage failed [%s]: %s", instrument,
                           payload.get("message", payload))
            return False
        logger.info("Leverage set: %s × %d", instrument, leverage)
        return True
    except Exception as exc:
        logger.warning("set_leverage error [%s]: %s", instrument, exc)
        return False


def get_positions(instrument: str = None) -> list:
    """Return list of open perpetual positions from exchange."""
    api_key, secret = _get_keys()
    params = {}
    if instrument:
        params["instrument_name"] = instrument
    body = _sign("private/get-positions", params, api_key, secret)
    try:
        r = requests.post(f"{_PRIVATE}/get-positions", json=body, timeout=10)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code", 0) != 0:
            raise ValueError(f"get-positions error: {payload.get('message', payload)}")
        return payload.get("result", {}).get("data", [])
    except Exception as exc:
        logger.warning("get_positions error: %s", exc)
        return []


def place_perp_order(
    instrument: str,
    side: str,
    order_type: str,
    quantity: float,
    ref_price: float = None,
    ref_price_type: str = "MARK_PRICE",
) -> dict:
    """Place a perpetual futures order.

    order_type: "MARKET" | "STOP_LOSS" | "TAKE_PROFIT"
    ref_price:  trigger price for STOP_LOSS / TAKE_PROFIT orders
    """
    api_key, secret = _get_keys()
    params: dict = {
        "instrument_name": instrument,
        "side":            side,
        "type":            order_type,
        "quantity":        str(round(quantity, 8)),
    }
    if ref_price is not None:
        params["ref_price"]      = str(round(ref_price, 2))
        params["ref_price_type"] = ref_price_type

    body = _sign("private/create-order", params, api_key, secret)
    r    = requests.post(f"{_PRIVATE}/create-order", json=body, timeout=15)
    r.raise_for_status()
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(
            f"Perp order rejected [{order_type} {side}]: {payload.get('message', payload)}"
        )
    logger.info("Perp order placed: %s %s %s qty=%.6f", order_type, side, instrument, quantity)
    return payload.get("result", {})


def cancel_all_orders(instrument: str) -> bool:
    """Cancel all open orders for a perpetual instrument."""
    api_key, secret = _get_keys()
    params = {"instrument_name": instrument}
    body   = _sign("private/cancel-all-orders", params, api_key, secret)
    try:
        r = requests.post(f"{_PRIVATE}/cancel-all-orders", json=body, timeout=10)
        r.raise_for_status()
        ok = r.json().get("code", -1) == 0
        if ok:
            logger.info("Cancelled all orders for %s", instrument)
        return ok
    except Exception as exc:
        logger.warning("cancel_all_orders error [%s]: %s", instrument, exc)
        return False


def get_derivatives_balance() -> dict:
    """Fetch derivatives wallet balance (separate from spot account).

    Returns {"available": float, "total": float, "equity": float}
    or empty dict on failure.
    """
    api_key, secret = _get_keys()
    body = _sign("private/user-balance", {}, api_key, secret)
    try:
        r = requests.post(f"{_PRIVATE}/user-balance", json=body, timeout=10)
        r.raise_for_status()
        payload = r.json()
        if payload.get("code", 0) != 0:
            raise ValueError(f"user-balance error: {payload.get('message', payload)}")
        for acc in payload.get("result", {}).get("data", []):
            if acc.get("currency") in ("USD", "USDT"):
                return {
                    "available": float(acc.get("available_balance", 0)),
                    "total":     float(acc.get("total_available_balance",
                                               acc.get("balance", 0))),
                    "equity":    float(acc.get("total_equity",
                                               acc.get("balance", 0))),
                }
        return {}
    except Exception as exc:
        logger.warning("get_derivatives_balance error: %s", exc)
        return {}
