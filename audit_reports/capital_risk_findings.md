# Capital Risk Findings

## CRITICAL — Fixed

### CR-1: Halt System Fail-Open (FIXED)
**File:** `runtime/orchestrator.py:329`  
**Severity:** CRITICAL  
**Impact:** Trades could be placed during governance failures  
**Root cause:** `_is_globally_halted()` returned `False` on any exception from governance module  
**Fix:** Returns `True` on exception — fail-safe  

### CR-2: Unhedged Position Tracked (FIXED)
**File:** `trading/cryptocom_bot.py:583`  
**Severity:** CRITICAL  
**Impact:** Bot tracked positions without SL/TP on exchange, could not manage risk  
**Root cause:** Code continued to `state.open_positions.append(pos)` after `sl_tp_ok=False`  
**Fix:** Return immediately without tracking — operator alerted to manual close  

### CR-3: DCA Race Condition (FIXED)
**File:** `trading/cryptocom_bot.py:708`  
**Severity:** CRITICAL  
**Impact:** DCA slot cleared even when live orders failed — position state mismatch  
**Root cause:** `pos["dca_size"] = 0.0` ran outside `if dca_ok:` block  
**Fix:** Moved inside lock, under `if dca_ok:` guard  

## HIGH — Pending (Wave 2)

### CR-4: Capital State Lost on Restart
**File:** `risk/capital_preservation.py`  
**Severity:** HIGH  
**Impact:** Engine restarts fresh as SAFE even after CRITICAL/HALT was triggered  
**Root cause:** `CapitalPreservationEngine` state stored only in-memory  
**Required fix:** Persist state to `data/capital_state.json` on every transition; load on startup  

### CR-5: Executor SL Failure Doesn't Cancel Entry
**File:** `trading/executor.py:75`  
**Severity:** HIGH  
**Impact:** If SL order fails, TP still attempted; if TP also fails, naked position on exchange  
**Root cause:** `try` blocks for SL and TP are independent  
**Required fix:** If SL fails, cancel entry order and return error; don't attempt TP  

### CR-6: alltime_peak Private Field Access (Race at Init)
**File:** `risk/capital_preservation.py:231`  
**Severity:** HIGH  
**Impact:** `_alltime_peak` seed bypasses internal lock — race if `record()` called during init  
**Root cause:** `self._drawdown_tracker._alltime_peak = starting_equity` accesses private attribute  
**Required fix:** Add `initialize_peak(equity)` public method to `DrawdownTracker`  

## MEDIUM — Capital Calculation Risks

### CR-7: Demo Balance Grows Unbounded
**Impact:** Risk-based sizing grows with total_pnl; after winning sessions, sizes become unrealistic  
**Fix:** Add daily balance reset in demo mode, or cap balance at starting 2×  

### CR-8: Size-Zero Silent Rejection
**Impact:** If sl_usd=0, position silently skipped — user expects trade to have opened  
**Fix:** Log WARNING with reason before returning  

### CR-9: ATR Zero → Constant 1% SL
**Impact:** In zero-volatility periods, ATR-based SL becomes 1% regardless of actual range  
**Fix:** Use minimum ATR of `0.5% of price` as floor  
