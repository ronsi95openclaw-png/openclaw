# Decisions

## 2026-05-29 — Adapt the RONSI95 template, don't execute it literally
- **Decision:** Reconcile the generic master prompt against the real machine and build
  only the missing, infra-available pieces into the existing repos.
  **Reason:** Every path was hardcoded to `C:\Users\ronsi95\` (user is `ronsi95openclaw`);
  `RONSI95-OS` doesn't exist; the active vault, HaulYeah bot, and crypto bot already exist.
  Literal execution would create an orphaned, broken parallel copy.

## 2026-05-29 — No Supabase
- **Decision:** Skip all Supabase deliverables (CRM schema, shared `database.py`, Supabase
  `/report` and morning briefing). **Reason:** Neither bot uses Supabase — HaulYeah uses
  Google Sheets + local JSON; the crypto bot uses local JSON only.

## 2026-05-29 — Quote/review as pure standalone helpers
- **Decision:** `agents/quote.py` and `agents/review.py` are dependency-free pure functions,
  not hardcoded with the template's phone/email/review URL.
  **Reason:** Testability and reuse; avoid baking in possibly-wrong contact details. Review
  URL is a parameter (intended source: `GOOGLE_REVIEW_URL`). Not yet wired into OutreachAgent.

## 2026-05-29 — Watchdog is alert-only; detection fixed
- **Decision:** Watchdog alerts on a down bot but does NOT auto-restart; detection matches
  the real process command line (`content.receiver`), not `tasklist` IMAGENAME.
  **Reason:** Unattended restart of a trading process is risky. The template's `tasklist`
  check could never match a script name, so it was a silent no-op.

## 2026-05-29 — Circuit breaker measures drawdown from a fixed start
- **Decision:** Halt trades in `execute_signals` when portfolio falls `MAX_DRAWDOWN_PCT`
  below `STARTING_BALANCE_USD` (defaults 20% / $96). Stateless, from fixed start (not a
  rolling high-water mark). **Reason:** Matches the template's intent; simple and testable.

## 2026-05-29 — `/report` is activity-only
- **Decision:** Report trade counts/volume/by-coin from local `data/trades.json`, not
  win-rate/P&L. **Reason:** The bot places market orders without exit tracking, so realized
  P&L is not computable — fabricating it would be misleading.
