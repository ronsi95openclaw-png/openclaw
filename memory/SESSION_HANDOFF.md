# SESSION HANDOFF — 2026-05-30

## Current State
- **Mode:** ClawBot DEMO (workflow notes), `TRADING_MODE` env var actually unset — worth normalizing later
- **Auth:** Crypto.com returns 401 — keys present in `.env` (22 / 28 chars) but rejected
- **Balance baseline:** `STARTING_BALANCE_USD=96.00` (placeholder, not real)
- **Strategy:** 4 candidates exist (`liquidity_sweep`, `trend_continuation`, `breakout_expansion`, `ema_momentum`) + RSI+MACD baseline in `trading/strategy.py`. None wired into executor.
- **Data:** `data/backtest/` has 8 files. `*_4h_1y.json` cap at 49 days (API pagination unsupported). `*_1d_1y.json` give ~299 days — usable for regime test.

## Open Problems
1. Crypto.com 401 (waiting on key refresh in `.env.new`)
2. `STARTING_BALANCE_USD` still placeholder
3. `infra/run_strategy_comparison.py` not yet built
4. `memory/` was missing — created today

## Done This Session
- Bootstrapped `memory/` directory (this file, CHANGES.md, DECISIONS.md, ACTIVE_TASKS.md, strategy/)
- Fixed Unicode crash in `infra/verify_cryptocom_auth.py`
- Decided 1d candles over 4h for regime test (see DECISIONS.md)
- Built `infra/run_strategy_comparison.py` (5 strategies × 4 symbols + 4-quarter BTC regime test)
- Ran the comparison — Phase 5D escalation triggered (no strategy hit 3/4 quarters)
- Documented strategy decision in `memory/strategy/backtest-2026-05-30.md`
- Decision: stay DEMO, paper-watch LiquiditySweep for ~14 days, NO executor wiring

## Did NOT Do (intentional)
- Did not wire any strategy into `trading/executor.py`
- Did not flip `TRADING_MODE` to LIVE
- Did not refresh Crypto.com keys (Ronnie's `.env.new` still had old values; deferred)
- Did not patch a "daily routine" file (none exists; the verifier is the auth check)

## Next Session Priorities
1. Resume Phase 1: re-verify Crypto.com auth (run `python -m infra.verify_cryptocom_auth`)
2. If auth passes: update `STARTING_BALANCE_USD` from the verifier's suggested value
3. After ~14 days of paper-watching: tally LiquiditySweep's live signals vs. backtest expectation
4. If live cadence ≈ backtest cadence → consider small surgical wire-in (DEMO only)
5. If live cadence diverges → escalate to ensemble approach (Phase 5D option 2)
