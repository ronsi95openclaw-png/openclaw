# Portfolio Exposure Engine — Report
**File:** `risk/portfolio_risk.py`
**Date:** 2026-05-22

## Problem
`CapitalPreservationEngine` tracked per-engine equity drawdown, but had no awareness of cross-position portfolio exposure. Three simultaneous long positions on BTC+ETH+SOL (0.85 correlation) under 3× leverage represented far more correlated risk than the per-position SL percentages suggested.

## What Was Built

### `PortfolioRiskEngine`
Thread-safe risk aggregation over all open positions.

**Core metrics:**
```python
get_total_portfolio_risk(balance) -> dict:
    total_notional      # sum of abs(size * price * leverage) across all positions
    long_notional       # long-only notional
    short_notional      # short-only notional
    net_notional        # long - short (directional exposure)
    leverage_ratio      # total_notional / balance
    correlation_risk_score  # 0.0–1.0 (1.0 = all positions same direction, fully correlated)
    max_single_symbol_pct   # largest single symbol as % of total_notional
```

**Regime-aware exposure caps:**
| Regime | Cap (× balance) |
|--------|----------------|
| TRENDING_BEAR | 1.5× |
| RANGING, MEAN_REVERTING, VOL_COMPRESSION, UNKNOWN | 2.0× |
| All others (TRENDING_BULL, MOMENTUM etc.) | 2.5× |

**Correlation model:**
- BTC, ETH, SOL treated as a single correlated group (ρ = 0.85)
- `correlation_risk_score = directional_fraction × 0.85`
- `directional_fraction = max(long_count, short_count) / total_count`

**Key methods:**
- `update_positions(positions, prices)` — refreshes state (thread-safe)
- `should_reduce_positions(balance, regime) → bool` — called before every `_open_position()`
- `get_regime_exposure(regime) → {cap_pct, current_pct, within_limits}`
- `get_correlation_risk() → {correlated_symbols, max_correlated_fraction, within_limits, correlation_score}`

## Integration
`CryptoComBot._open_position()` calls `should_reduce_positions()` before opening. If exposure is breached, the position is silently blocked with a WARNING log.

## Chaos Test Coverage
- No positions → zero exposure
- Missing prices → falls back to `entry_price`
- Zero balance → returns False (no crash)
- All-same-direction → correlation_risk_score > 0.5
- Opposing directions → net_notional < total_notional
- TRENDING_BEAR has lower cap than TRENDING_BULL

## Impact
Prevents scenarios where 3 simultaneous long positions (all on correlated crypto) represent 7.5× effective balance exposure under 3× leverage.
