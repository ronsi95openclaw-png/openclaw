"""
ClawBot — Trade Executor
Executes BUY/SELL orders on Crypto.com via private API.
Only fires on HIGH confidence RSI+MACD signals.
Logs every action to data/logs/trades.log.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger("clawbot.trading.executor")

_LOG_DIR  = Path(__file__).parent.parent / "data" / "logs"
_LOG_FILE = _LOG_DIR / "trades.log"
_PRIVATE  = "https://api.crypto.com/v2/private"

# Minimum order sizes (USDT) per coin — Crypto.com minimums
_MIN_ORDER_USD = {
    "BTC_USDT": 10.0,
    "ETH_USDT": 10.0,
    "SOL_USDT": 5.0,
    "XRP_USDT": 5.0,
}


def _log_trade(entry: dict) -> None:
    """Append trade result to trades.log."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now(timezone.utc).isoformat()
    line = f"TRADE_DECISION | {ts} | {json.dumps(entry)}\n"
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info(f"Trade logged: {entry}")


def _place_order(instrument: str, side: str, notional_usd: float, price: float) -> dict:
    """
    Place a market order on Crypto.com.

    Args:
        instrument:   e.g. "BTC_USDT"
        side:         "BUY" or "SELL"
        notional_usd: USD value to size the order from
        price:        Current price, used to convert notional_usd into a
                      base-currency quantity for SELL orders

    Returns:
        API response dict with order details.
    """
    from trading.exchange import _get_keys, _sign

    api_key, secret = _get_keys()

    # Crypto.com v2 MARKET orders take `notional` (quote currency, e.g. USDT)
    # for BUY but `quantity` (base currency, e.g. BTC) for SELL — sending
    # `notional` on a SELL is rejected by the exchange, which would leave a
    # LIVE position that entered but can never exit via this path.
    params = {
        "instrument_name": instrument,
        "side":            side,
        "type":            "MARKET",
    }
    if side == "SELL":
        if not price or price <= 0:
            raise ValueError(f"Cannot size MARKET SELL for {instrument}: invalid price {price}")
        params["quantity"] = str(round(notional_usd / price, 8))
    else:
        params["notional"] = str(round(notional_usd, 2))

    # Retry transient network errors ONCE, and only when we know the request
    # never reached the exchange. A ReadTimeout is ambiguous — the order may
    # already have been accepted server-side — so it is NOT retried here;
    # only ConnectionError/ConnectTimeout (failed before the request landed)
    # are safe to retry. Create-order is not idempotent, and each attempt is
    # freshly signed since the signature is bound to a nonce/timestamp.
    r = None
    for attempt in range(2):
        body = _sign("private/create-order", params, api_key, secret)
        try:
            r = requests.post(f"{_PRIVATE}/create-order", json=body, timeout=15)
            break
        except (requests.ConnectionError, requests.ConnectTimeout) as exc:
            if attempt == 1:
                raise
            logger.warning(f"_place_order transient network error (attempt 1/2): {exc}")
            time.sleep(1.0)

    r.raise_for_status()  # 4xx/5xx after success on the wire -> raise immediately, NO retry
    payload = r.json()

    if payload.get("code", 0) != 0:
        raise ValueError(f"Order rejected: {payload.get('message', payload)}")

    return payload.get("result", {})


def execute_signal(signal, portfolio_usd: float) -> dict:
    """
    Execute a BUY or SELL signal from RSIMACDStrategy.

    Args:
        signal:        Signal object with .coin, .action, .confidence
        portfolio_usd: Total portfolio value in USD (for position sizing)

    Returns:
        Result dict with status, order_id, amount, etc.
    """
    from trading.strategy import calculate_position_size
    from trading.mode import get_mode

    coin   = signal.coin
    action = signal.action
    mode   = get_mode()

    # Only execute HIGH confidence — protect capital
    if signal.confidence != "HIGH":
        msg = f"Skipped {action} {coin} — confidence {signal.confidence} (need HIGH)"
        logger.info(msg)
        _log_trade({"action": "SKIP", "coin": coin, "reason": msg, "confidence": signal.confidence, "mode": mode})
        return {"status": "skipped", "reason": msg}

    # Position sizing: 1.5% of portfolio per trade
    from trading.exchange import fetch_ticker_price
    try:
        price = fetch_ticker_price(coin)
    except Exception as e:
        msg = f"Price fetch failed for {coin}: {e}"
        _log_trade({"action": "ERROR", "coin": coin, "reason": msg, "mode": mode})
        return {"status": "error", "reason": msg}

    sizing     = calculate_position_size(portfolio_usd, price, risk_pct=1.5)
    usd_amount = sizing["usd_amount"]
    min_order  = _MIN_ORDER_USD.get(coin, 5.0)

    if usd_amount < min_order:
        msg = f"Order too small: ${usd_amount} < min ${min_order} for {coin}"
        _log_trade({"action": "SKIP", "coin": coin, "reason": msg, "mode": mode})
        return {"status": "skipped", "reason": msg}

    # Simulate without placing a real order unless mode is exactly "LIVE" —
    # fail-safe so a corrupted/unexpected mode value never reaches the
    # exchange (trading/mode.py already guards this too; belt and suspenders).
    if mode != "LIVE":
        entry = {
            "action":     action,
            "coin":       coin,
            "usd_amount": usd_amount,
            "price":      price,
            "rsi":        round(signal.rsi, 2),
            "confidence": signal.confidence,
            "order_id":   "DEMO",
            "status":     "demo",
            "mode":       mode,
        }
        _log_trade(entry)
        try:
            from trading.history import record_trade
            record_trade(entry)
        except Exception as exc:
            logger.warning(f"Trade history record failed: {exc}")
        logger.info(f"[DEMO] Simulated {action} {coin} ${usd_amount} @ ${price}")
        return entry

    try:
        result = _place_order(coin, action, usd_amount, price=price)
        entry  = {
            "action":     action,
            "coin":       coin,
            "usd_amount": usd_amount,
            "price":      price,
            "rsi":        round(signal.rsi, 2),
            "confidence": signal.confidence,
            "order_id":   result.get("order_id", "unknown"),
            "status":     "executed",
            "mode":       "LIVE",
        }
        _log_trade(entry)
        try:
            from trading.history import record_trade
            record_trade(entry)
        except Exception as exc:
            logger.warning(f"Trade history record failed: {exc}")
        logger.info(f"Order executed: {action} {coin} ${usd_amount}")
        return entry

    except Exception as exc:
        entry = {
            "action":  action,
            "coin":    coin,
            "status":  "error",
            "reason":  str(exc),
            "mode":    "LIVE",
        }
        _log_trade(entry)
        logger.error(f"Order failed: {exc}")
        return entry


def _notify(message: str) -> None:
    """Best-effort Telegram alert; never raises."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except Exception as exc:
        logger.warning(f"Alert send failed: {exc}")


def execute_signals(signals: list, portfolio_usd: float) -> list[dict]:
    """Execute a list of signals. Halts entirely if the circuit breaker trips."""
    from trading.risk import circuit_breaker_message, is_circuit_tripped

    if is_circuit_tripped(portfolio_usd):
        logger.error(f"Circuit breaker tripped — halting trades. portfolio=${portfolio_usd:.2f}")
        _log_trade({"action": "HALT", "reason": "circuit_breaker", "portfolio_usd": portfolio_usd})
        _notify(circuit_breaker_message(portfolio_usd))
        return [{"status": "halted", "reason": "circuit_breaker", "portfolio_usd": portfolio_usd}]

    results = []
    for signal in signals:
        if signal.action in ("BUY", "SELL"):
            result = execute_signal(signal, portfolio_usd)
            results.append(result)
    return results
