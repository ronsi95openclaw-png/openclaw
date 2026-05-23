# Audit Report — Balance Feed Wiring (Phase 8)
**Date:** 2026-05-23
**File:** `runtime/balance_feed.py`
**Status:** IMPLEMENTED · TESTED · 8/8 PASSING
**Risk Addressed:** R-04 (balance feed not wired into live guardian)

## Summary
`BalanceFeedDaemon` is a background thread that periodically calls
`trading.exchange.get_derivatives_balance()`, feeds the result into
`LiveBalanceGuardian.run_check()`, and tracks consecutive failure counts.
The daemon lifecycle (start / stop / force_check) is fully thread-safe.
DEMO_MODE advisory contract is inherited from the guardian (HALT downgraded to CRITICAL).

## Architecture

```
BalanceFeedDaemon (background thread, interval_s default=60s)
    │
    ├── _fetch_equity()          lazy import of trading.exchange
    │       └── get_derivatives_balance() → {"equity": float, ...}
    │
    └── _fetch_and_check(equity) → runtime.live_balance_guardian.get_guardian()
                                        └── run_check(exchange_balance=equity)
```

## Exchange Fetch Path

`get_derivatives_balance()` returns a dict. The daemon reads `result["equity"]`
(float). If the key is absent or the import fails, `_fetch_equity()` returns `None`
and the consecutive_failures counter is incremented.

Key path:
```
trading/exchange.py → get_derivatives_balance()
    └── Crypto.com REST /v1/private/get-account-summary (PERP margin)
```

In DEMO_MODE, the exchange module returns paper account balances (no real funds).

## BalanceFeedStatus (dataclass, 7 fields)
- `running`: bool — True when daemon thread is alive
- `consecutive_failures`: int — reset to 0 on any successful fetch
- `total_checks`: int — monotonically increasing counter
- `last_fetch_ts`: Optional[str] — ISO8601 timestamp of last attempt
- `last_equity`: Optional[float] — last successfully fetched equity value
- `last_error`: Optional[str] — last exception message, or None
- `demo_mode`: bool — reflects constructor argument

## Error Handling
- Import error on `trading.exchange` → `_fetch_equity()` returns None (no crash)
- Exception in `get_derivatives_balance()` → caught, logged, returns None
- Exception in guardian `run_check()` → caught, logged; failure NOT counted
  (exchange fetch succeeded; guardian is advisory)
- Daemon thread exception → logged; thread exits cleanly; `is_running()` → False

## DEMO_MODE Advisory Contract
- Daemon runs identically in DEMO_MODE=true and DEMO_MODE=false
- Guardian enforces downgrade: HALT severity → CRITICAL in DEMO_MODE
- No halt marker written to disk in DEMO_MODE (guardian contract)
- All audit JSONL writes execute normally in both modes

## Thread Safety
- `_lock: threading.Lock` guards `_status` mutations
- `_stop_event: threading.Event` used for clean shutdown (no Thread.daemon flag)
- `stop(timeout_s)` joins thread with finite timeout; warns if timeout exceeded
- `force_check()` calls `_fetch_and_check()` synchronously (safe from any thread)

## Singleton Access
```python
from runtime.balance_feed import get_balance_feed_daemon
daemon = get_balance_feed_daemon()   # returns module-level singleton
daemon.start()
```

`get_balance_feed_daemon()` uses double-checked locking. Safe for concurrent callers.

## How to Start the Daemon
```python
# In dashboard/api/server.py startup lifespan:
from runtime.balance_feed import get_balance_feed_daemon
daemon = get_balance_feed_daemon(interval_s=60, demo_mode=True)
daemon.start()

# On shutdown:
daemon.stop(timeout_s=5.0)
```

Or via CLI for one-shot check:
```bash
python3 -c "from runtime.balance_feed import get_balance_feed_daemon; \
    d = get_balance_feed_daemon(); d.force_check(); print(d.get_status())"
```

## Test Results (8/8)
| Test | Result |
|------|--------|
| test_daemon_starts_and_stops_cleanly | PASSED |
| test_fetch_equity_returns_none_on_import_error | PASSED |
| test_fetch_equity_returns_float_on_success | PASSED |
| test_consecutive_failures_tracked | PASSED |
| test_consecutive_failures_reset_on_success | PASSED |
| test_get_status_returns_dataclass | PASSED |
| test_force_check_increments_total_checks | PASSED |
| test_demo_mode_flag_preserved | PASSED |
