# Exchange Metadata Registry — Phase 4
**File:** `runtime/exchange_metadata.py`
**Date:** 2026-05-23

## Problem
Order quantity precision was fixed at 8 decimal places across all instruments (`round(quantity, 8)` in executor.py). Crypto.com perpetual instruments have strict per-instrument quantity increments:
- BTCUSD-PERP: 0.001 BTC increment (3dp)
- ETHUSD-PERP: 0.01 ETH increment (2dp)
- SOLUSD-PERP: 1 SOL increment (0dp)

Using rounding instead of truncation could inflate order sizes past the intended risk amount. Using wrong precision causes order rejection by the exchange.

## What Was Built

### `InstrumentSpec` Dataclass
```python
instrument_name: str
qty_precision: int       # decimal places for quantity
price_precision: int     # decimal places for price
min_qty: float           # minimum order size
max_leverage: int        # max allowed leverage
tick_size: float         # minimum price movement
lot_size: float          # minimum quantity increment
min_notional: float      # minimum order value (USD)
supported_order_types: List[str]  # ["MARKET", "LIMIT", "STOP_LOSS", "TAKE_PROFIT"]
leverage_brackets: List[dict]     # tiered leverage brackets
last_refreshed_ts: float          # Unix timestamp of last API refresh
```

### Hardcoded Defaults (permanent safety fallback)
| Instrument | qty_precision | price_precision | min_qty | max_leverage | min_notional |
|-----------|--------------|----------------|---------|-------------|-------------|
| BTCUSD-PERP | 3 | 1 | 0.001 | 100 | 10.0 |
| ETHUSD-PERP | 2 | 2 | 0.01 | 100 | 10.0 |
| SOLUSD-PERP | 0 | 3 | 1.0 | 20 | 10.0 |

### `ExchangeMetadataRegistry`
- `normalize_quantity(instrument, qty)` — **TRUNCATION** via `math.floor(qty * 10**precision) / 10**precision`
  - NOT `round()` — truncation ensures we never exceed the intended risk amount
  - Raises `ValueError` if result < min_qty
- `normalize_price(instrument, price)` — `round(price, price_precision)`
- `validate_order(instrument, qty, price, leverage)` — 3-gate: min_qty, max_leverage, min_notional
- `get_spec(instrument)` — triggers auto-refresh if data older than `refresh_interval_hours`
- `refresh()` — live call to Crypto.com `/get-instruments?type=PERPETUAL_SWAP`; falls back to defaults on failure
- Dual-form resolution: both `BTC_USDT` and `BTCUSD-PERP` map to same spec

### Integration into executor.py
```python
# Before every place_perp_order() call:
try:
    from runtime.exchange_metadata import get_registry
    qty = get_registry().normalize_quantity(instrument, qty)
except Exception as _norm_exc:
    logger.warning("normalize_quantity failed — using raw qty")

# min_qty check uses ExchangeMetadataRegistry instead of hardcoded dict
```

**No order may reach the exchange without this normalization gate.**

## Soak Test Verification
- `test_exchange_metadata_precision`:
  - BTC 0.12345678 → 0.123 (3dp truncation) ✅
  - ETH 1.99999 → 1.99 (2dp truncation) ✅
  - SOL 2.9 → 2.0 (0dp truncation) ✅
  - Both ticker form (BTC_USDT) and canonical form (BTCUSD-PERP) resolve correctly ✅
  - `validate_order()` rejects qty below min_qty ✅
  - `normalize_price()` rounds (not truncates) correctly ✅
