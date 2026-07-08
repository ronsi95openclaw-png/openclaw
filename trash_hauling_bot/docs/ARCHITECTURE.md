# HaulYeah Bot — Architecture (audited 2026-07-08)

One-page map of the lead pipeline, the compliance guardrails, and known issues.
Companion to `CLAUDE.md` (conventions) and `COMPLIANCE.md` (hard rules).

## Runtime

- Entry: `main.py` → APScheduler (asyncio) + Telegram bot, single process.
- Launched at logon by the `HaulYeahBot` scheduled task → `haulyeah-hidden.vbs`
  → `start_haulyeah.bat` (supervisor loop, restarts `main.py` after 15s on exit,
  logs to `data/supervisor.log` / `stdout.log` / `stderr.log`).
- `start_haulyeah_guarded.bat` is the duplicate-safe wrapper (detects an existing
  bot by venv ExecutablePath — two instances collide on the Telegram token, 409).
- Config: `config.py` dataclass, loads the consolidated `..\.env`
  (namespaced `TRASH_BOT_*` / `FB_*` / `GOOGLE_*` keys). `DRY_RUN=true` runs
  fully in-memory (no Google creds / FB session needed).

## Lead pipeline (scrape → filter → Sheets → human outreach)

1. **Scrape** — `agents/scraper.py` (`fb_scraper` job, every
   `SCRAPER_INTERVAL_MINUTES`, default 30). Playwright headless Chromium with the
   persistent FB session in `data/fb_profile/`. Read-only Marketplace search per
   keyword, jittered sleeps (2–4s per keyword, 1.2–2s per scroll), max 25 links
   per keyword. Detects the login wall and bails (audit: `login_required`).
2. **Filter** — `_is_demand_lead()` in `scraper.py`: rejects container/rental,
   supply-side, and for-sale posts; requires an explicit demand phrase AND a
   junk/hauling topical word. Rejections audited with reason.
   Text is passed through `utils/sanitize.py` (HTML strip, length cap, prompt-
   injection detection) and scored for urgency (1–10) and size (1–10).
3. **Dedup** — `utils/dedup.py`, sha256 fingerprint of url+title persisted in
   `data/seen_listings.json`.
4. **Sheets** — `integrations/sheets.py` writes to the "Leads" worksheet
   (`COLUMNS` schema, status machine: new → outreach_queued → outreach_sent →
   responded/scheduled/completed/declined/no_response/cancelled). All remote
   calls wrapped in `utils/retry.with_retry`. `sanitize_lead_field` raises on
   prompt-injection content, so injected leads never reach the sheet.
5. **Outreach (draft-only)** — `agents/outreach.py`: `/outreach <lead_id>` drafts
   a message (Claude `claude-haiku-4-5` with template fallback; injection-flagged
   descriptions never reach the LLM), queues it in `data/pending_outreach.json`,
   and posts it to Telegram with Confirm/Deny buttons.
   **`confirm_send` performs no network send** — it only marks the sheet row
   `outreach_sent` after a human `/confirm`. The human sends manually.
6. **Schedule** — `agents/calendar_sync.py` + `integrations/gcal.py`: `/schedule`
   creates a Google Calendar event (3h block, America/Chicago); `calendar_sync`
   job backfills events for scheduled rows every 5 min; `lead_aging` job marks
   new/outreach_sent rows older than `LEAD_STALE_DAYS` (7) as no_response daily.
7. **Ops helpers** — `agents/quote.py` (tiered price estimate, `/quote`, and the
   `OUTREACH_INCLUDE_QUOTE`-gated line in outreach drafts), `agents/review.py`
   (`/review` draft, completed jobs only), `utils/revenue_ledger.py` (CLI,
   `data/revenue.json`).

## Compliance guardrails in place (COMPLIANCE.md)

- **No send path exists in code.** Nothing in agents/ or integrations/ sends
  SMS/email/Messenger or posts to FB. `confirm_send` is a status update only.
- **Scraper is read-only + paced**: never clicks message/contact, jittered
  sleeps, capped listings per keyword, login-wall bail-out.
- **Human-in-the-loop**: outreach exists only as a Telegram-reviewed draft queue;
  `/confirm`, `/deny`, inline buttons — all audit-logged.
- **Audit**: `utils/audit.py` appends JSONL to `data/audit.log` for every scan,
  lead add/reject, draft, confirm, deny, schedule, cancel, startup/shutdown.
- **Injection defense**: `utils/sanitize.py` regex screen; flagged text is kept
  out of both the LLM prompt and the sheet.
- **Guard library (currently unwired)**: `utils/compliance.py`
  (`assert_human_approved`, `is_outbound_allowed`, `human_pace_sleep`,
  ALLOWED/FORBIDDEN action sets) is fully tested but **only imported by tests**.
  Production code doesn't call it yet — acceptable today because no send path
  exists, but any future send/post code MUST route through it per COMPLIANCE.md.
  Note: `human_pace_sleep` is blocking (`time.sleep`) — in the async scraper use
  `asyncio.sleep(random.uniform(...))` (current pattern) or an async wrapper.

## Known issues (2026-07-08 audit)

- **Token in logs (fixed forward)**: httpx INFO lines wrote the full bot-token
  getUpdates URL into `data/bot.log` every ~10s. `main.py` now silences httpx
  below WARNING (takes effect on next restart). Existing `bot.log`/`stdout.log`
  still contain the token — consider deleting/rotating them, and rotating the
  Telegram token if those logs ever leave the machine.
- **No log rotation**: `bot.log`/`stdout.log`/`stderr.log`/`audit.log` are
  22–44 MB and grow unbounded (supervisor appends forever). Needs
  RotatingFileHandler + supervisor-side truncation.
- **Unwired "Piece 2/3" modules**: `utils/scoring.py` (rank_leads 0–100 digest)
  and `utils/quicksend.py` (copy block + sms:/m.me one-tap link) are complete and
  tested but not wired into `telegram_bot.py`. The live `/topleads` uses a
  simpler urgency+size sum; there is **no `/quicksend` command** despite memory
  notes claiming one.
- **Pricing drift**: `agents/quote.py` PRICING ($89/$199/$349/$499/$699 tiers)
  disagrees with `brand_kit.json` `pricing_summary` (base $75 + $25/item).
  One of them is stale — business decision needed.
- **Brand-name drift**: customer-facing drafts say "HaulYeah"
  (`quote.format_quote`, `review.py` defaults, `/review` handler) while
  `brand_kit.json` brands as "Haul Y'all".
- **`/topleads` crash edge**: `int(l.get("urgency_score") or 0)` in
  `telegram_bot.py` raises on non-numeric sheet cells (e.g. "5.0"); caught by
  the global error handler but the command fails.
- **`gcal._parse_dt` silent fallback**: an unparseable `scheduled_datetime`
  silently books tomorrow instead of erroring.
- **Second `main.py` process**: on 2026-07-08 a second `python main.py`
  (system Python 3.13, NOT the bot venv) was running alongside the bot with the
  same start time. The guarded launcher only detects venv-path pythons. Verify
  what it is; if it's a duplicate HaulYeah it will fight for the Telegram token.
- **Stale docs**: `memory/*` last updated 2026-05-29 (predates quicksend/scoring
  /compliance work); `test_full_system.py` sets `DOTENV_PATH_OVERRIDE`, which
  nothing reads.
