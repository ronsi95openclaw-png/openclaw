# Chaos Test Results
**Date:** 2026-05-22  **Run:** `pytest tests/chaos/ -v`

## Result: 39/39 PASSED

---

## `tests/chaos/test_exchange_failures.py` — 21 tests

### `TestFetchTickerChaos` (6 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_429_raises_http_error` | ✅ PASS | Rate limit → HTTPError propagates |
| `test_503_raises_http_error` | ✅ PASS | Service unavailable → HTTPError propagates |
| `test_empty_data_raises_value_error` | ✅ PASS | Empty `data: []` → ValueError |
| `test_timeout_propagates` | ✅ PASS | requests.Timeout propagates to caller |
| `test_malformed_json_raises` | ✅ PASS | JSONDecodeError propagates |
| `test_zero_values_returned_intact` | ✅ PASS | Zero bid/ask not masked by `or` (regression guard) |

### `TestFetchCandlesChaos` (5 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_403_raises_http_error` | ✅ PASS | Forbidden → HTTPError |
| `test_empty_candles_raises` | ✅ PASS | Empty candle list → ValueError |
| `test_api_error_code_raises` | ✅ PASS | API error code in 200 response → ValueError |
| `test_partial_candle_fields_handled` | ✅ PASS | `open`/`high`/`low`/`close` keys instead of `o`/`h`/`l`/`c` |
| `test_network_error_propagates` | ✅ PASS | ConnectionError propagates |

### `TestGetOpenOrdersChaos` (3 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_timeout_returns_empty_list` | ✅ PASS | Timeout → swallowed → [] (safe default) |
| `test_503_returns_empty_list` | ✅ PASS | 503 → [] |
| `test_api_error_returns_empty_list` | ✅ PASS | 40401 auth error → [] |

### `TestReconciliationChaos` (4 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_exchange_timeout_marks_unreachable` | ✅ PASS | Exchange down → `exchange_reachable=False` |
| `test_demo_mode_skips_exchange` | ✅ PASS | Demo mode never calls exchange |
| `test_corrupt_local_position_flagged` | ✅ PASS | Positions missing required fields → CORRUPT_STATE mismatch |
| `test_demo_mode_passes_with_valid_positions` | ✅ PASS | Well-formed demo positions → passed=True |

### `TestPortfolioRiskChaos` (6 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_no_positions_zero_exposure` | ✅ PASS | Empty positions → notional=0, leverage=0 |
| `test_missing_price_uses_entry_price` | ✅ PASS | No prices dict → falls back to entry_price |
| `test_zero_balance_does_not_crash` | ✅ PASS | balance=0 → returns False, no division error |
| `test_all_same_direction_max_correlation` | ✅ PASS | 3 longs → correlation_risk_score > 0.5 |
| `test_opposing_directions_reduce_net_exposure` | ✅ PASS | Long + short → net < total |
| `test_trending_bear_regime_lower_cap` | ✅ PASS | BEAR cap (1.5×) < BULL cap (2.5×) |

---

## `tests/chaos/test_capital_chaos.py` — 18 tests

### `TestCapitalStateChaos` (6 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_concurrent_updates_no_crash` | ✅ PASS | 100 threads updating equity simultaneously |
| `test_drawdown_triggers_halt` | ✅ PASS | 26% drawdown → EMERGENCY_HALT |
| `test_state_never_upgrades_on_single_update` | ✅ PASS | One bounce doesn't reset DEFENSIVE/CRITICAL to SAFE |
| `test_loss_streak_progression` | ✅ PASS | 5 consecutive losses escalate state |
| `test_persist_and_reload` | ✅ PASS | State survives process restart |
| `test_zero_equity_goes_to_halt` | ✅ PASS | Equity → 0 triggers HALT |

### `TestReplayValidatorChaos` (5 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_empty_journal_passes` | ✅ PASS | Empty file → passed=True, 0 events |
| `test_missing_file_handled` | ✅ PASS | Missing path → graceful fail with description |
| `test_corrupt_json_line_flagged` | ✅ PASS | Invalid JSON line → ERROR |
| `test_time_backwards_flagged` | ✅ PASS | ts=2000 before ts=1000 → WARNING |
| `test_illegal_capital_transition_flagged` | ✅ PASS | EMERGENCY_HALT → SAFE → ERROR |

### `TestShadowOptimizationChaos` (4 tests)
| Test | Result | Scenario |
|------|--------|---------|
| `test_large_weight_jump_rejected` | ✅ PASS | Δweight=0.35 → rejected at gate 3 |
| `test_low_trades_rejected` | ✅ PASS | 3 trades < 10 minimum → rejected |
| `test_rollback_restores_snapshot` | ✅ PASS | rollback() preserves original weight |
| `test_concurrent_candidates_safe` | ✅ PASS | 5 concurrent apply_candidate() → no errors |

---

## Key Findings
1. **Exchange failure handling is correct**: 429/503/timeout errors are either propagated (public endpoints) or safely swallowed (private/write endpoints that return [])
2. **Zero-value regression** confirmed fixed: `fetch_ticker` correctly returns `bid=0.0` without masking via `or`
3. **Capital state machine is deterministic**: concurrent updates don't corrupt state; halt is irreversible from HALT state
4. **Portfolio risk handles edge cases**: zero balance, missing prices, empty position set all handled without exceptions
5. **Reconciliation is demo-safe**: never calls exchange in demo mode
