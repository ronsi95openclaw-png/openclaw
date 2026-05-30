# Active Tasks

_Last updated: 2026-05-29_

## Done (this session)
- [x] Quote estimator `agents/quote.py` (+16 tests)
- [x] Review-request message `agents/review.py` (+6 tests)
- [x] Crypto watchdog `infra/watchdog.py` (+9 tests) and run/install `.bat`s
- [x] Rate-limit backoff `trading/backoff.py` wrapping exchange calls (+14 tests)
- [x] Circuit breaker `trading/risk.py` wired into `execute_signals` (+15 tests)
- [x] Trade history `trading/history.py` + `/report` command (+13 tests)
- [x] Vault sync script `infra/sync_to_vault.bat` (-> Documents\Obsidian Vault)

## Next up
- [ ] Push branch + open PR (awaiting explicit "yes push")
- [ ] Run `infra/install_watchdog.bat` once to schedule the watchdog (system change)
- [ ] (Optional) wire quote/review helpers into `OutreachAgent`
- [ ] (Optional) add exit/position tracking so `/report` can show real P&L

## Not doing (deferred — see DECISIONS.md)
- New Obsidian vault / Supabase CRM / lead_alert.py / Supabase morning briefing
- Backtest rebuild (template strategy API differs from this repo's RSIMACDStrategy)
