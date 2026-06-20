# Active Tasks

_Last updated: 2026-06-20_

## Done (2026-06-20 — haulyall marketplace/outreach session)
- [x] Fix "hermes" lead-alert truncation: `agents/lead_alert.py` bounded digest (+9 tests)
- [x] `/digest` command surfaces the length-safe alert in Telegram
- [x] DFW outreach + Meta ad copy + carousel: `agents/marketing.py` (+17 tests)
- [x] `/pitch [city]` and `/ads` Telegram commands
- [x] `MARKETING_PLAYBOOK.md` — copy/paste assets + Ads Manager targeting notes
- [x] Expanded `FB_SEARCH_KEYWORDS` default for container/drop-off haul jobs

## Next up (needs owner)
- [ ] Run `python -m agents.scraper --login` locally once (browser needed; can't be done in CI)
- [ ] Approve pushing Meta ads live (needs Meta ad account + Page) → then ads MCP can create them
- [ ] Point the external `haulyeah-lead-alert` cron at the compact digest output

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
