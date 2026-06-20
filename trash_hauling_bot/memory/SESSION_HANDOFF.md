# Session Handoff

_Last updated: 2026-06-20_

## 2026-06-20 — haulyall marketplace / outreach / ads
Decoded the request: "the hermes" = the Telegram bot `Ronsi95.hermes.bot`; its scheduled
`haulyeah-lead-alert` cron was failing with `Response remained truncated after 3 continuation
attempts` (alert payload outgrew Telegram's limit).

Shipped on branch `claude/haulyall-marketplace-outreach-il62wq`:
- `agents/lead_alert.py` — `build_digest()`: urgency-ranked, hard-capped, "+N more" footer →
  fixes the truncation. Surfaced as `/digest`.
- `agents/marketing.py` — DFW outreach scripts (F-150 + drop-off container), Meta ad copy
  (3 variants), 5 carousel cards. Surfaced as `/pitch [city]` and `/ads`.
- `MARKETING_PLAYBOOK.md` — human-readable copy + Ads Manager targeting.
- `config.py` — expanded default `FB_SEARCH_KEYWORDS` for container/drop-off jobs.
- Tests: +26 (lead_alert 9, marketing 17). Local run: 97 pass (2 files need playwright/
  anthropic, not installed in the cloud container — pre-existing, unrelated).

Could NOT do, by design/constraint:
- Log into the owner's FB account / browse Marketplace live — no browser in the cloud
  container and it's against Meta's terms to drive a personal account from an agent. The
  existing `--login` scraper flow is the supported path; owner runs it locally once.
- Create live Meta ads — needs the owner's ad account + Page approval; copy is ready and the
  ads MCP can push it once approved.

---

_Earlier — 2026-05-29_

## What this session did
Adapted the generic "RONSI95 AI OS" master prompt (Parts 1 & 2) onto the **real**
codebase instead of building the parallel `C:\Users\ronsi95\RONSI95-OS\` workspace
the template assumed (wrong username, no Supabase, no Ollama on PATH, vault already
exists). User chose "adapt to reality."

Built the genuinely-missing, infra-available deliverables, test-first, one commit each:

| Deliverable | Repo area | Commit |
|---|---|---|
| Quote estimator (keyword->price tier) | `trash_hauling_bot/agents/quote.py` | `7b4a45c` |
| Post-job review-request message | `trash_hauling_bot/agents/review.py` | `7b4a45c` |
| Process watchdog + Telegram alert | `infra/watchdog.py` (+ `.bat`s) | `f8fb9d3` |
| Rate-limit backoff on Crypto.com calls | `trading/backoff.py` -> `exchange.py` | `7cbfcc4` |
| Circuit breaker (drawdown halt) | `trading/risk.py` -> `executor.py` | `e6b4182` |
| Trade history + `/report` command | `trading/history.py`, `content/receiver.py` | `7908abc` |
| Vault sync script (-> Documents\Obsidian Vault) | `infra/sync_to_vault.bat` | (this session) |

Tests: HaulYeah 90 pass, crypto bot 51 pass.

## Current state
- Working branch: `claude/trash-hauling-bot-YpknV` (PR base is `feature/telegram-notifications`).
- All work committed locally. **Not pushed** — awaiting explicit "yes push".
- `quote.py` / `review.py` are standalone helpers; NOT yet wired into `OutreachAgent`
  (outreach still uses Claude/template messages). Wiring is optional next step.

## Open problems / not done (deferred on purpose)
- New Obsidian vault, Supabase CRM schema, `lead_alert.py`, Supabase morning briefing,
  and the template's `test_connections.py` — all skipped (duplicate existing work or
  depend on Supabase/Ollama this machine doesn't use).
- Backtest deferred: template's API (`evaluate_signal` + candle JSON) differs from this
  repo's `RSIMACDStrategy` — a heavier rebuild.
- `/report` is activity-only (counts/volume), NOT win-rate/P&L — bot has no exit tracking.
- Watchdog scheduled task NOT installed (run `infra/install_watchdog.bat` manually).

## Next priorities
1. (If wanted) wire `quote.estimate` / `review.review_request_message` into outreach flow.
2. Install watchdog scheduled task when ready.
3. Decide whether to add exit/position tracking so `/report` can show real P&L.
