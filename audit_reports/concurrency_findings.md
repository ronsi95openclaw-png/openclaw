# Concurrency & Async Findings

## CRITICAL — Fixed

### CC-1: EventBus TOCTOU Loop-None Dereference (FIXED)
**File:** `dashboard/api/event_bus.py:53`  
**Pattern:** Check `_loop is not None`, then call `_loop.call_soon_threadsafe()` — another thread could set `_loop = None` between check and call  
**Fix:** Snapshot `loop` and `queues` atomically under lock before any use  

### CC-2: DCA State Mutation Outside Lock (FIXED)
**File:** `trading/cryptocom_bot.py:708`  
**Pattern:** `pos["dca_size"] = 0.0` ran after `if dca_ok:` block without lock  
**Fix:** Moved into `with self._lock:` under `if dca_ok:` guard  

### CC-3: Trade ID Counter Race (FIXED)
**File:** `trading/cryptocom_bot.py`  
**Pattern:** Two threads generating `CX{strategy}{timestamp}` in the same second  
**Fix:** Global `_trade_id_lock` + monotonic counter suffix  

### CC-4: Exchange Nonce Collision (FIXED)
**File:** `trading/exchange.py:40`  
**Pattern:** `int(time.time() * 1000)` — two concurrent API calls in same millisecond share nonce  
**Fix:** `_next_nonce()` with atomic counter, always strictly increasing  

## HIGH — Not Yet Fixed

### CC-5: Emergency Halt Race at Release
**File:** `governance/emergency_controls.py:199`  
**Pattern:** Approval check and halt release are in separate lock scopes — another thread can change state between them  
**Fix:** Hold single lock across entire check→release sequence  

### CC-6: Capital State Update Non-Atomic with Journal
**File:** `runtime/orchestrator.py:235`  
**Pattern:** `old_state = get_state()` then `update()` then `new_state = get_state()` — another thread can update state between reads  
**Fix:** Add `record_transition()` method to CapitalEngine that returns old+new state atomically  

### CC-7: Ruflo RPC Response Mismatch
**File:** `runtime/ruflo_bridge.py:309`  
**Pattern:** Concurrent RPC calls can receive each other's responses — responses discarded without logging  
**Fix:** Per-request response queues keyed by request ID  

## MEDIUM — Not Yet Fixed

### CC-8: Partial TP PnL Uses Potentially Stale Entry Price
**File:** `trading/cryptocom_bot.py:723`  
**Pattern:** `partial_pnl = mult_p * (price - pos["entry_price"]) * half_size` — if DCA updated `entry_price` concurrently, calculation uses stale value  
**Fix:** Read `entry_price` inside `with self._lock:` before calculation  

### CC-9: Date Flush Two-Thread Race
**File:** `trading/cryptocom_bot.py:283`  
**Pattern:** Catch-up flush thread starts; if scan loop hits another date boundary before flush finishes, two flushes race  
**Fix:** Guard flush with `threading.Event` — only one flush at a time  

### CC-10: Google Sheets Worker Sleep Loop
**File:** `reporting/google_sheets.py`  
**Pattern:** Fixed 15s retry sleep on sustained outage  
**Fix:** Exponential backoff with cap  

## LOW

### CC-11: `is_active()` Without Lock
**File:** `runtime/orchestrator.py:106`  
**Pattern:** `return self._active` — no lock, but in practice only the bot thread changes it  
**Fix:** Acquire `self._lock` for consistency  
