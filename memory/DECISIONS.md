# DECISIONS

Architecture choices and the reasoning behind them. Append-only.

Entry format:
```
## [YYYY-MM-DD] — <Decision title>
**Decision:** what was decided
**Why:** reasoning (constraints, trade-offs, alternatives considered)
**How to apply:** when this decision becomes load-bearing
**Status:** STANDING | SUPERSEDED-BY-<link> | REVERTED
---
```

---

## [2026-05-30] — Backtest uses 1d candles instead of 4h for the regime test
**Decision:** Use the existing `*_1d_1y.json` files (300 candles × ~299 days) as the primary dataset for the 5-strategy comparison and 4-quarter regime test
**Why:**
- Crypto.com's public `get-candlestick` endpoint caps at 300 candles/call and does NOT support `end_ts` pagination on the public path (hard-won lesson, documented in [fetch_historical_candles.py:33](../infra/fetch_historical_candles.py#L33))
- 4h candles thus cap at ~50 days of coverage — not enough to span multiple market regimes, which was the whole point of pulling more data
- 1d candles give ~299 days = roughly 10 months → covers at least 2-3 regime shifts
- The strategies in `trading/strategies/` are timeframe-agnostic (they consume a list of closes); the test still answers the regime-resilience question
**How to apply:** Phase 4 comparison runner reads `*_1d_1y.json`, not `*_4h_1y.json`. If we later want 4h granularity for a winner, that's a separate "deep-dive" backtest on the most recent 50 days.
**Alternatives considered:**
- Re-running the 4h fetcher → would just confirm the 50-day cap, no new information
- Sourcing 4h data from another exchange (Binance public) → cross-exchange data introduces basis noise; deferred unless 1d test is inconclusive
**Status:** STANDING
---

## [2026-05-30] — Defer strategy-wiring; paper-watch LiquiditySweep in DEMO for 2 weeks
**Decision:** Do NOT wire any of the 5 backtested strategies into `trading/executor.py` this session. Stay in DEMO. Observe LiquiditySweep's HIGH-confidence signals for ~14 days against live market data before any executor change.
**Why:**
- Phase 5D escalation triggered: no strategy hit the 3/4-quarters regime resilience bar (best was LiquiditySweep at 1/4)
- LiquiditySweep had the strongest per-symbol signal (SOL 75%, XRP 100%) and only positive aggregate PnL (+$0.777 across all 4 symbols), so it's the candidate worth watching
- But the 1d/300-candle dataset gives ~35 effective candles per regime quarter — too small for a confident wiring call
- Wiring on weak data + then "verifying via paper trade" inverts the safety check: the executor is the live blast radius, so it stays clean until evidence is convincing
**How to apply:** Next session reads live signal log, compares to backtest cadence (~17 trades expected over a full year). If live behavior matches backtest within reasonable tolerance, then a small surgical wire-in (still DEMO mode) becomes the next step. If it diverges, escalate to ensemble (≥2 strategies must agree).
**Alternatives considered:**
- Wire now, paper-trade later → rejected per workflow hard rule #4
- Ensemble approach → deferred; needs LiquiditySweep paper-watch first to know if its signals are even worth aggregating
- Halt strategy work entirely → rejected; LiquiditySweep's signal is suggestive enough to keep observing
**Status:** STANDING (revisit ~2026-06-13)
**Artifact:** [memory/strategy/backtest-2026-05-30.md](strategy/backtest-2026-05-30.md), `data/backtest/comparison_20260530-2119.json`
---
