
---
## [2026-06-17] — PROPOSAL: Add MACD to LiquiditySweepStrategy (Category B — awaiting "yes apply")

**Problem:** `trading/strategies/liquidity_sweep.py` hardcodes `macd=0.0, macd_signal_val=0.0, macd_histogram=0.0` in every `Signal()` return. `calculate_macd()` exists in `trading/strategy.py` and is already imported (partially — `calculate_rsi` is, `calculate_macd` is not). 68 days of paper-watch data show MACD=0.0 for all signals.

**Effect:** Confidence upgrades that depend on MACD confirmation are impossible. MEDIUM is the max achievable. HIGH signals require RSI divergence only (not MACD) per current code, but the MACD fields are dead weight in every output row and in Telegram signal alerts.

**Proposed fix — 3-line change to `trading/strategies/liquidity_sweep.py`:**

```python
# Line 35 — change:
from trading.strategy import Signal, calculate_rsi
# To:
from trading.strategy import Signal, calculate_rsi, calculate_macd

# In evaluate(), after `current_rsi = self._safe_rsi(...)`, add:
try:
    macd_val, macd_sig, macd_hist = calculate_macd(
        closes, fast=12, slow=26, signal_period=9
    )
except ValueError:
    macd_val, macd_sig, macd_hist = 0.0, 0.0, 0.0

# Replace all Signal(..., macd=0.0, macd_signal_val=0.0, macd_histogram=0.0, ...)
# with: Signal(..., macd=macd_val, macd_signal_val=macd_sig, macd_histogram=macd_hist, ...)
```

**Risk:** Low. Paper-watch only — no trade execution. The MACD values will populate correctly for any run after the fix. `calculate_macd` requires `slow + signal_period = 35` closes; we fetch 100 (warmup already satisfied). Confidence logic is unchanged — HIGH still requires RSI divergence. MACD data is now informational (visible in Telegram alerts and JSONL output).

**Status:** PENDING — say "yes fix MACD" to apply
