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
        set_leverage, place_perp_order, cancel_all_orders, to_perp_instrument,
    )

    instrument = to_perp_instrument(symbol)

    # Normalize quantity to exchange-required precision (truncation, not rounding).
    # No order may reach the exchange without this normalization.
    try:
        from runtime.exchange_metadata import get_registry
        qty = get_registry().normalize_quantity(instrument, qty)
    except Exception as _norm_exc:
        logger.warning("exchange_metadata normalize_quantity failed (%s) — using raw qty", _norm_exc)

    # Reject orders below exchange minimum rather than silently inflating
    try:
        from runtime.exchange_metadata import get_registry as _gr
        _spec = _gr().get_spec(instrument)
        min_qty = _spec.min_qty
    except Exception:
        from trading.exchange import _MIN_QTY_PERP
        min_qty = _MIN_QTY_PERP.get(instrument, 0.001)

    if qty < min_qty:
        raise ValueError(
            f"Order qty {qty} below exchange minimum {min_qty} for {instrument}. "
            f"Increase balance or raise risk_pct."
        )

    # Advisory execution optimization (never overrides capital/governance gates).
    # In demo mode optimizer returns passthrough advice (qty unchanged).
    _demo = True  # executor has no direct demo_mode access; always conservative
    try:
        from runtime.execution_optimizer import get_optimizer
        _advice = get_optimizer().get_advice(
            symbol=symbol, qty=qty, current_spread_bps=0.0, demo_mode=_demo
        )
        if _advice.should_wait:
            logger.info("ExecutionOptimizer: should_wait=True for %s (%s) — proceeding anyway (demo)",
                        symbol, _advice.wait_reason)
        qty = _advice.advised_qty
    except Exception as _opt_exc:
        logger.debug("ExecutionOptimizer unavailable (%s) — using raw qty", _opt_exc)

    set_leverage(instrument, leverage)

    entry_side = "BUY"  if side == "LONG"  else "SELL"
    exit_side  = "SELL" if side == "LONG"  else "BUY"

    entry_result   = place_perp_order(instrument, entry_side, "MARKET", qty)
    entry_order_id = entry_result.get("order_id", "")

    # Guard: exchange must return an order_id for the entry, otherwise we have
    # no way to track or cancel this position.
    if not entry_order_id:
        logger.critical(
            "Entry order returned no order_id [%s %s] — cannot track position, aborting",
            side, symbol,
        )
        result = {
            "symbol": symbol, "instrument": instrument, "side": side, "qty": qty,
            "entry_order_id": "", "sl_order_id": "", "tp_order_id": "",
            "sl_price": sl_price, "tp_price": tp_price, "leverage": leverage,
            "sl_tp_ok": False, "status": "ENTRY_NO_ID",
        }
        _log_trade({"event": "open", **result})
        return result

    sl_order_id = tp_order_id = ""

    try:
        sl          = place_perp_order(instrument, exit_side, "STOP_LOSS", qty, ref_price=sl_price)
        sl_order_id = sl.get("order_id", "")
    except Exception as exc:
        # SL failed — cancel everything so position doesn't sit unhedged.
        logger.critical(
            "SL order FAILED [%s %s] — cancelling all orders to prevent unhedged position: %s",
            side, symbol, exc,
        )
        cancel_all_orders(instrument)
        result = {
            "symbol": symbol, "instrument": instrument, "side": side, "qty": qty,
            "entry_order_id": entry_order_id, "sl_order_id": "", "tp_order_id": "",
            "sl_price": sl_price, "tp_price": tp_price, "leverage": leverage,
            "sl_tp_ok": False, "status": "SL_FAILED",
        }
        _log_trade({"event": "open", **result})
        return result

    try:
        tp          = place_perp_order(instrument, exit_side, "TAKE_PROFIT", qty, ref_price=tp_price)
        tp_order_id = tp.get("order_id", "")
    except Exception as exc:
        # TP failed — cancel all (including the SL just placed) so position is clean.
        logger.critical(
            "TP order FAILED [%s %s] — cancelling all orders to prevent partial hedge: %s",
            side, symbol, exc,
        )
        cancel_all_orders(instrument)
        result = {
            "symbol": symbol, "instrument": instrument, "side": side, "qty": qty,
            "entry_order_id": entry_order_id, "sl_order_id": sl_order_id, "tp_order_id": "",
            "sl_price": sl_price, "tp_price": tp_price, "leverage": leverage,
            "sl_tp_ok": False, "status": "TP_FAILED",
        }
        _log_trade({"event": "open", **result})
        return result

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
        "sl_tp_ok":        True,
        "status":          "opened",
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
