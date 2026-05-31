# ACTIVE TASKS

What's in flight right now. Update as work moves.

## In Progress
- **Auth fix (deferred):** `.env.new` saved but contains identical (still-broken) keys. Ronnie to re-paste fresh credentials when ready; verifier will confirm.

## Next Up
- After auth: update `STARTING_BALANCE_USD` to real Crypto.com balance (Phase 2)
- ~2 weeks: tally LiquiditySweep's live DEMO signals vs. backtest expectation, then make wiring call

## Done (this session)
- Memory scaffold (CHANGES, DECISIONS, SESSION_HANDOFF, ACTIVE_TASKS, strategy/)
- Unicode fix in `infra/verify_cryptocom_auth.py`
- 1d candle decision over 4h (DECISIONS.md)
- Built + ran strategy comparison (5 × 4 + regime test)
- Decision documented: stay DEMO, paper-watch LiquiditySweep

## Blocked
- Phase 1 / Phase 2 → still waiting for Ronnie to put fresh Crypto.com credentials into `.env.new`
