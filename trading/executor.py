"""ClawBot — Perpetual Futures Executor

Executes LONG/SHORT positions on Crypto.com perpetual futures (BTCUSD-PERP etc.)
with native SL/TP orders placed on the exchange at open time.

Flow (live):
  open_position()  →  set_leverage  →  market entry  →  STOP_LOSS order
                                                       →  TAKE_PROFIT order
  close_position() →  cancel_all_orders  →  market close

Demo mode is handled entirely in cryptocom_bot.py; this module is live-only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("openclaw.trading.executor")

_LOG_DIR  = Path(__file__).parent.parent / "data" / "logs"
_LOG_FILE = _LOG_DIR / "trades.log"


def _log_trade(entry: dict) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"TRADE | {ts} | {json.dumps(entry)}\n")
    logger.info("Trade logged: %s", entry)


def open_position(
    symbol: str,
    side: str,           # "LONG" or "SHORT"
    sl_price: float,
    tp_price: float,
    qty: float,          # base-currency quantity already calculated by bot risk model
    leverage: int = 3,
) -> dict:
    """Open a perpetual futures position with native SL/TP orders.

    qty is the base-currency amount calculated by _calc_size() in the bot — the
    executor uses it directly so the exchange position matches bot state exactly.

    Returns dict with entry order ID, SL/TP order IDs, quantity, and
    a boolean 'sl_tp_ok' so the caller can detect unhedged positions.
    """
    from trading.exchange import (
        set_leverage, place_perp_order, to_perp_instrument, _MIN_QTY_PERP,
    )

    instrument = to_perp_instrument(symbol)
    min_qty    = _MIN_QTY_PERP.get(instrument, 0.001)

    # Reject orders below exchange minimum rather than silently inflating
    if qty < min_qty:
        raise ValueError(
            f"Order qty {qty} below exchange minimum {min_qty} for {instrument}. "
            f"Increase balance or raise risk_pct."
        )

    set_leverage(instrument, leverage)

    entry_side = "BUY"  if side == "LONG"  else "SELL"
    exit_side  = "SELL" if side == "LONG"  else "BUY"

    entry_result   = place_perp_order(instrument, entry_side, "MARKET", qty)
    entry_order_id = entry_result.get("order_id", "")

    sl_order_id = tp_order_id = ""

    try:
        sl          = place_perp_order(instrument, exit_side, "STOP_LOSS", qty, ref_price=sl_price)
        sl_order_id = sl.get("order_id", "")
    except Exception as exc:
        logger.critical("SL order FAILED [%s] — position is UNHEDGED: %s", symbol, exc)

    try:
        tp          = place_perp_order(instrument, exit_side, "TAKE_PROFIT", qty, ref_price=tp_price)
        tp_order_id = tp.get("order_id", "")
    except Exception as exc:
        logger.critical("TP order FAILED [%s] — position has no take-profit: %s", symbol, exc)

    sl_tp_ok = bool(sl_order_id and tp_order_id)
    if not sl_tp_ok:
        logger.critical(
            "UNHEDGED POSITION OPENED [%s %s] qty=%.6f  sl_ok=%s  tp_ok=%s — "
            "manual intervention required",
            side, symbol, qty, bool(sl_order_id), bool(tp_order_id),
        )

    result = {
        "symbol":          symbol,
        "instrument":      instrument,
        "side":            side,
        "qty":             qty,
        "entry_order_id":  entry_order_id,
        "sl_order_id":     sl_order_id,
        "tp_order_id":     tp_order_id,
        "sl_price":        sl_price,
        "tp_price":        tp_price,
        "leverage":        leverage,
        "sl_tp_ok":        sl_tp_ok,
        "status":          "opened" if sl_tp_ok else "UNHEDGED",
    }
    _log_trade({"event": "open", **result})
    return result


def close_position(symbol: str, side: str, quantity: float) -> dict:
    """Close a perpetual position (cancels pending SL/TP orders first).

    side: "LONG" or "SHORT" — the existing position direction.
    """
    from trading.exchange import (
        place_perp_order, cancel_all_orders, to_perp_instrument,
    )

    instrument = to_perp_instrument(symbol)
    cancel_all_orders(instrument)

    close_side = "SELL" if side == "LONG" else "BUY"
    result = place_perp_order(instrument, close_side, "MARKET", quantity)
    order_id = result.get("order_id", "")

    _log_trade({"event": "close", "symbol": symbol, "instrument": instrument,
                "side": side, "qty": quantity, "order_id": order_id})
    return {"status": "closed", "order_id": order_id}


# ── Legacy spot shim (backwards compatibility) ────────────────────────────────

def _place_order(instrument: str, side: str, notional_usd: float) -> dict:
    """Legacy spot market order — kept for any callers not yet migrated."""
    import requests
    from trading.exchange import _get_keys, _sign

    api_key, secret = _get_keys()
    params = {
        "instrument_name": instrument,
        "side":            side,
        "type":            "MARKET",
        "notional":        str(round(notional_usd, 2)),
    }
    body = _sign("private/create-order", params, api_key, secret)
    r    = requests.post("https://api.crypto.com/exchange/v1/private/create-order",
                         json=body, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if payload.get("code", 0) != 0:
        raise ValueError(f"Order rejected: {payload.get('message', payload)}")
    return payload.get("result", {})
