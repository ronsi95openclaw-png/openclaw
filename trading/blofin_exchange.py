"""BloFin exchange connector — public & private REST endpoints.

Public:  candles, ticker, funding rate (no auth)
Private: balance, positions, place order (HMAC-SHA256 + Base64)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid

import requests

logger = logging.getLogger("clawbot.trading.blofin")

_BASE = "https://openapi.blofin.com"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_keys() -> tuple[str, str, str]:
    key        = os.getenv("BLOFIN_API_KEY",    "").strip()
    secret     = os.getenv("BLOFIN_SECRET",     "").strip()
    passphrase = os.getenv("BLOFIN_PASSPHRASE", "").strip()
    return key, secret, passphrase


def _sign_headers(method: str, path: str, body: str,
                  key: str, secret: str, passphrase: str) -> dict:
    ts    = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    prehash = ts + nonce + method.upper() + path + (body or "")
    sig = base64.b64encode(
        hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "ACCESS-KEY":        key,
        "ACCESS-SIGN":       sig,
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-NONCE":      nonce,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type":      "application/json",
    }


# ── Public endpoints ──────────────────────────────────────────────────────────

def fetch_candles(symbol: str, bar: str = "15m", limit: int = 100) -> list[dict]:
    """OHLCV candles, oldest first. symbol e.g. 'BTC-USDT'."""
    path = f"/api/v1/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    r = requests.get(_BASE + path, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise ValueError(f"BloFin candles error [{symbol}]: {data.get('msg')}")
    # BloFin returns newest-first — reverse to oldest-first
    raw = list(reversed(data.get("data", [])))
    return [
        {
            "ts":     int(c[0]),
            "open":   float(c[1]),
            "high":   float(c[2]),
            "low":    float(c[3]),
            "close":  float(c[4]),
            "volume": float(c[5]),
        }
        for c in raw
    ]


def fetch_ticker(symbol: str) -> dict:
    """Latest bid/ask/last + 24h change for a symbol."""
    path = f"/api/v1/market/tickers?instId={symbol}"
    r = requests.get(_BASE + path, timeout=8)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise ValueError(f"BloFin ticker error [{symbol}]: {data.get('msg')}")
    raw = data.get("data", [{}])[0]
    return {
        "last":       float(raw.get("last", 0)),
        "bid":        float(raw.get("bidPx", 0)),
        "ask":        float(raw.get("askPx", 0)),
        "volume_24h": float(raw.get("vol24h", 0)),
        "change_24h": float(raw.get("changeRate24h", 0) or 0),
    }


def fetch_funding_rate(symbol: str) -> float:
    """Current funding rate for a perpetual futures symbol."""
    path = f"/api/v1/public/funding-rate?instId={symbol}"
    r = requests.get(_BASE + path, timeout=8)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        return 0.0
    raw = data.get("data", [{}])[0]
    try:
        return float(raw.get("fundingRate", 0) or 0)
    except (ValueError, TypeError):
        return 0.0


# ── Private endpoints ─────────────────────────────────────────────────────────

def get_balance() -> dict:
    """USDT balance. Returns {'usdt': available, 'total': equity, 'unrealized_pnl': upl}."""
    key, secret, passphrase = _get_keys()
    if not key:
        raise EnvironmentError("BLOFIN_API_KEY not set")
    path    = "/api/v1/account/balance"
    headers = _sign_headers("GET", path, "", key, secret, passphrase)
    r = requests.get(_BASE + path, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise ValueError(f"BloFin balance error: {data.get('msg')}")
    details = data.get("data", [{}])[0].get("details", [])
    for asset in details:
        if asset.get("currency", "").upper() == "USDT":
            return {
                "usdt":           float(asset.get("available", 0) or 0),
                "total":          float(asset.get("equity",    0) or 0),
                "unrealized_pnl": float(asset.get("upl",       0) or 0),
            }
    return {"usdt": 0.0, "total": 0.0, "unrealized_pnl": 0.0}


def get_positions() -> list[dict]:
    """All open positions."""
    key, secret, passphrase = _get_keys()
    if not key:
        return []
    path    = "/api/v1/account/positions"
    headers = _sign_headers("GET", path, "", key, secret, passphrase)
    r = requests.get(_BASE + path, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        return []
    positions = []
    for p in data.get("data", []):
        size = float(p.get("positions", 0) or 0)
        if size == 0:
            continue
        positions.append({
            "symbol":          p.get("instId", ""),
            "side":            p.get("posSide", "long"),
            "size":            abs(size),
            "entry_price":     float(p.get("avgPx",  0) or 0),
            "unrealized_pnl":  float(p.get("upl",    0) or 0),
            "leverage":        float(p.get("lever",  1) or 1),
            "margin":          float(p.get("margin", 0) or 0),
        })
    return positions


def place_order(
    symbol: str,
    side: str,             # "buy" | "sell"
    size: float,           # number of contracts
    sl_price: float | None = None,
    tp_price: float | None = None,
    leverage: int = 3,
) -> dict:
    """Market order with optional SL/TP on isolated margin."""
    key, secret, passphrase = _get_keys()
    if not key:
        raise EnvironmentError("BLOFIN_API_KEY not set")

    path   = "/api/v1/trade/order"
    params: dict = {
        "instId":     symbol,
        "marginMode": "isolated",
        "posSide":    "long" if side == "buy" else "short",
        "side":       side,
        "orderType":  "market",
        "size":       str(size),
        "leverage":   str(leverage),
    }
    if sl_price:
        params["slTriggerPrice"] = str(round(sl_price, 6))
        params["slOrderPrice"]   = "-1"   # market fill
    if tp_price:
        params["tpTriggerPrice"] = str(round(tp_price, 6))
        params["tpOrderPrice"]   = "-1"   # market fill

    body    = json.dumps(params)
    headers = _sign_headers("POST", path, body, key, secret, passphrase)
    r = requests.post(_BASE + path, headers=headers, data=body, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise ValueError(f"BloFin order error: {data.get('msg')}")
    return data.get("data", [{}])[0]


def test_connection() -> dict:
    """Verify API credentials by fetching balance. Returns {'ok': bool, 'msg': str}."""
    try:
        bal = get_balance()
        return {"ok": True, "msg": f"Connected — ${bal['usdt']:.2f} USDT available"}
    except EnvironmentError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:
        return {"ok": False, "msg": f"Auth failed: {str(e)[:120]}"}
