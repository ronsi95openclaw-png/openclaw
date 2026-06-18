# MASTER HANDOFF & ROADMAP — 2026-06-18

Single source of truth across **all three projects** (ClawBot trading, HaulYeah hauling,
Hermes overseer). Condenses ~13 prior Claude sessions into one resume point and lays out
exactly what's missing to finish the three goals Ronnie set.

> Read me first. Then `memory/ACTIVE_TASKS.md` for the live task list and
> `memory/SESSION_HANDOFF.md` for the prior (2026-05-31) end-of-session state.

---

## 0. TL;DR — where we are

| Project | State | One-line status |
|---|---|---|
| **ClawBot** (this repo) | DEMO, running | Trades scanned but **never sent to me as setups**. TJR = `liquidity_sweep.py` (built, paper-watched). |
| **HaulYeah** (`trash_hauling_bot/`) | DRY_RUN | FB Marketplace scraper **works**; ad posting **not built**; new-lead auto-alert **missing**. |
| **Hermes** (overseer) | **Does not exist yet** | Greenfield. Needs to be created from scratch (dashboard + Telegram + oversight loop). |
| **Dashboard** (`dashboard/app.py`) | Works | Boots, serves HTTP 200, renders 5 panels. Read-only. Only shows ClawBot — not the other 2 bots. |

**⚠️ OVERDUE:** The 2026-05-31 plan scheduled a LiquiditySweep paper-watch **Day-7 review (2026-06-07)** and
**Day-14 decision (2026-06-14)**. Today is **2026-06-18** — **both are past due.** First resume action should be
to read `data/paper_watch/liquidity_sweep.jsonl` and make the wire/extend/retire call (ACTIVE_TASKS #3 & #5).

---

## 1. Condensed session history (the "~10 sessions")

Chronological, deduped from `CHANGES.md` + both `SESSION_HANDOFF.md` files:

1. **2026-05-29** — Adapted the generic "RONSI95 AI OS" template onto the *real* repo. Built HaulYeah quote
   estimator, review-request message, watchdog+Telegram alert, Crypto.com backoff, circuit breaker, trade
   history + `/report`. Tests: HaulYeah 90 pass, crypto 51 pass.
2. **2026-05-30 (am)** — Fixed Unicode crash in `verify_cryptocom_auth`.
3. **2026-05-30 (pm)** — 5-strategy × 4-symbol backtest + 4-quarter regime test. **No strategy hit 3/4 regimes**
   (best: LiquiditySweep 1/4 but only positive aggregate PnL). Decision: stay DEMO, paper-watch LiquiditySweep 14d.
4. **2026-05-31 (00:00–00:15)** — Caught `.gitignore` gap (`.env` → `.env*`), 2 atomic commits.
5. **2026-05-31 (06:35)** — Built `infra/paper_watch_liquiditysweep.py` + daily 09:00 scheduled task. Caught a
   live MEDIUM BUY on XRP_USDT on first run.
6. **2026-05-31 (06:55)** — HANDS-OFF incident: another session was reorganizing the Obsidian vault; aborted vault writes.
7. **2026-05-31 (07:15)** — Vault all-clear, rebased, pushed vault `5d1d8a7`.
8. **2026-05-31 (18:30)** — **Crypto.com private API v1 → v2 migration** (new keys provisioned for v2; v1 returned
   `50001 ERR_INTERNAL`). Balance verified $96.39. `create-order` on v2 still **unverified** (gates LIVE).
9. **2026-05-31 (18:45–19:30)** — Built + validated `DAILY_ROUTINE.md`; pushed trailing docs commits.
10. **2026-06-01** — First real DAILY_ROUTINE run: 6/7 rules pass; fixed 2 template-vs-reality bugs (fictional
    `MAX_TRADE_RISK_PCT` env var; non-existent `agents.news_filter`).
11. **Branch consolidation** — work landed on `feature/telegram-notifications`; current dev branch is
    `claude/hermes-dashboard-bots-62u9ou`.

**Recurring lesson across sessions:** the template assumed infra that doesn't exist here (Supabase, Ollama on
PATH, a parallel workspace, `agents/` modules). **Always verify against the real repo before building.**

---

## 2. Goal 1 — ClawBot sends me TJR trade setups over Telegram

**Target:** the bot scans the market on the TJR setup and **DMs me a formatted trade setup** (pair, direction,
entry, stop, targets, confidence, reasoning). I then execute it manually in Claude chat via the **Liquid /
Hyperliquid MCP** (`mcp__700f63b4-*` — `suggest_trade`, `execute_order`, `get_portfolio`, etc. are all live here).

**What exists**
- ✅ **TJR = `trading/strategies/liquidity_sweep.py`** — confirmed. It's an ICT/smart-money liquidity-sweep + RSI
  divergence strategy. Produces `Signal(action, confidence, rsi, reason, coin)`. HIGH = sweep + divergence.
- ✅ Outbound Telegram send path: `trading/executor.py:_notify()` and `content/receiver.py:_scheduler_send()`.
- ✅ Scheduler (`core/scheduler.py`) with a daily autotrade job pattern to copy.
- ✅ Paper-watch logger already runs the strategy live daily.

**Gaps (concrete)**
1. `Signal` has **no entry/stop/target/RR fields** (`trading/strategy.py:48`). Add them, or a new `TradeSetup`.
2. **No setup math** — compute entry (last close), stop (swing low/high + buffer), targets (1R/2R/3R). New
   `trading/setup.py`.
3. **No TJR scan job** — add `_run_tjr_scan()` in `core/scheduler.py` (clone the autotrade job; **send-only, never
   execute** on Crypto.com — execution happens via Liquid MCP in chat).
4. **No setup formatter** — `to_trade_setup_message()` (Telegram HTML).
5. **No on-demand command** — `/tjr [1h|4h|1d]` in `content/receiver.py`.
6. **No setup log** — `data/logs/tjr_setups.jsonl`.

**Effort:** ~600 LOC across `trading/strategy.py`, new `trading/setup.py`, `core/scheduler.py`, `content/receiver.py`.

**Decision needed first:** is "execute in Claude chat via Liquid MCP" purely manual (bot just messages me — recommended),
or do we want a `/confirm`-gated bridge that calls the Liquid MCP? Manual is safer to ship first.

---

## 3. Goal 2 — HaulYeah scrapes FB Marketplace + posts ads

**Target (a):** scrape FB Marketplace for hauling jobs and **send them to me** so I can reach out and get hired
(I have an F150 + trash containers). **Target (b):** create and post hauling-service ads.

**What exists**
- ✅ **FB Marketplace scraper works** — `trash_hauling_bot/agents/scraper.py` (Playwright, persistent FB login,
  dedupe, urgency/size scoring → Google Sheets). One-time `python -m agents.scraper --login`.
- ✅ Outreach message generation (Claude + template) with injection guards + `/confirm` queue.
- ✅ Telegram command interface, Google Sheets lead DB, Google Calendar sync.

**Gaps (concrete)**
- ⚠️ **(a) ~90% done.** Missing: **auto-alert to me when new leads land.** Scraper finds leads but I have to
  poll `/leads new`. Fix: after a scrape with `new_leads > 0`, push a Telegram message to the owner chat. Small change.
- ❌ **(b) Ad posting = 0%.** BUT this environment has the **Meta/Facebook Ads MCP live** (`mcp__3b5cd3e0-*`:
  `ads_create_campaign`, `ads_create_ad_set`, `ads_create_creative`, `ads_create_ad`, `ads_get_ad_preview`,
  `ads_get_ad_accounts`, `ads_get_user_pages`, …). So ad creation can be driven **from Claude/Hermes directly via
  MCP** — no new bot code strictly required to *post*. What's missing is **config**: a Meta ad account ID + a
  Facebook Page + access token (now stubbed into `.env.example`, see §6), plus ad creative copy/image.
  Higgsfield MCP (`mcp__3d5c7322-*` `generate_image`) can produce the ad creative image.

**Important nuance:** FB **Marketplace** *listings* (the free peer-to-peer post) are **not** the same as FB **Ads**.
The Meta Ads MCP posts paid Ads (Feed/Marketplace placement), not a free Marketplace listing. Posting a *free*
Marketplace listing still requires the Playwright path (browser automation) the scraper already uses. **Recommend:
use the Meta Ads MCP for paid promotion** (cleaner, supported here) and decide separately if we want a Playwright
"post free listing" flow.

**Effort:** auto-alert ~30 LOC. Ad posting = config + an MCP-driven playbook (no/low new code).

---

## 4. Goal 3 — Hermes, the 24/7 personal overseer

**Target:** my personal agent that oversees ALL projects, with a working dashboard and its own Telegram bot.

**Reality check:** **Hermes does not exist in the codebase** (grep for "hermes" = 0 hits). This is greenfield.

**Proposed shape (for approval — see §7)**
- A new top-level `hermes/` package: a lightweight orchestrator that (1) periodically checks the health of ClawBot
  + HaulYeah (process alive, last-signal age, errors), (2) surfaces a morning briefing, (3) relays/routes alerts.
- **Telegram:** its own BotFather token (`HERMES_BOT_TOKEN`) so it's a distinct chat from ClawBot/HaulYeah.
- **Dashboard:** extend `dashboard/app.py` into a **multi-bot** view (add HaulYeah + Hermes panels) rather than
  standing up a second Flask app. Currently the dashboard only knows about ClawBot.
- **"24/7" loop:** in this Claude-Code-on-web environment, a true always-on process needs a host (Railway is
  already configured — `railway.toml`, `Procfile`, `nixpacks.toml`). Alternatively the `/loop` skill can run a
  recurring Hermes check on an interval during a session. Long-term: deploy to Railway.

**Gaps:** essentially everything — package scaffold, health-check module, briefing generator, Telegram wiring,
dashboard multi-bot extension, deploy target.

---

## 5. Dashboard test results (tested this session)

Booted `python dashboard/app.py` on :8080 in this container and hit it over HTTP (no browser/Playwright tool is
available in this remote env, so this is the closest "test like a human"):

- ✅ **HTTP 200**, ~5 KB, all 5 panels render: System Status / Live Prices / Brain Stats / Pending Reminders /
  Recent Trade Decisions. Auto-refresh + countdown work. Read-only, never writes — safe to run beside the bots.
- With no `.env`/`data/`/Ollama (fresh container) it correctly degrades: ClawBot **Idle**, Ollama **offline ❌**,
  Claude API **not set ⚠️**, prices "CoinGecko unavailable", reminders/trades empty. **No crashes.**

**"Missing settings" = environment, not code.** The dashboard needs a populated `.env` (Ronnie's secrets — can't
be committed) to light up. The real *structural* gap: it only monitors ClawBot. For Hermes to "oversee all
projects," it must add **HaulYeah** and **Hermes** panels (§4).

---

## 6. Settings added / still required

`.env.example` updated this session with the genuinely-missing blocks:
- **Meta/Facebook Ads** (HaulYeah ad posting via MCP): `FB_AD_ACCOUNT_ID`, `FB_PAGE_ID`, `FB_ACCESS_TOKEN`.
- **Hermes**: `HERMES_BOT_TOKEN`, `HERMES_CHAT_ID`, `HERMES_CHECK_INTERVAL_MINUTES`.

**Ronnie still must provide (secrets — never committed):**
- ClawBot: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`, `CRYPTOCOM_API_KEY/SECRET`.
- HaulYeah: `TRASH_BOT_TOKEN`, Google service-account JSON + `GOOGLE_SHEET_ID`, `FB_SEARCH_LOCATION`.
- HaulYeah ads: Meta ad account + Page + token (from the same Meta business that owns the Ads MCP connection).
- Hermes: a fresh BotFather token.

---

## 7. Recommended execution order (next sessions)

1. **Clear the overdue trading decision** — read `data/paper_watch/liquidity_sweep.jsonl`, count HIGH signals,
   make the wire/extend/retire call (ACTIVE_TASKS #3/#5). Cheap, unblocks Goal 1.
2. **Goal 1 (highest leverage):** build TJR→Telegram setup messaging (§2). Send-only; I execute via Liquid MCP.
3. **Goal 2a:** add HaulYeah new-lead auto-alert (§3) — tiny, high value.
4. **Goal 2b:** wire Meta Ads MCP playbook + creative gen once Ronnie supplies ad account/Page (§3, §6).
5. **Goal 3:** scaffold Hermes + extend dashboard to multi-bot (§4). Biggest build — do after 1–4 prove out.

**Open decisions for Ronnie (blockers to building):**
- Goal 1 — manual-execute (bot just DMs me) vs. a `/confirm` Liquid-MCP bridge? (Recommend manual first.)
- Goal 2b — paid Meta Ads (MCP-supported here) vs. free Marketplace listings (needs Playwright)?
- Goal 3 — extend the existing dashboard (recommended) vs. a separate Hermes app? Deploy to Railway for true 24/7?

---

## 8. Risks / carried-forward

- `private/create-order` on Crypto.com v2 **unverified** — hard gate before any LIVE flip (ACTIVE_TASKS #1).
- Mode is **DEMO**; do not flip to LIVE without §ACTIVE_TASKS #1 done.
- Vault `sync_to_vault.bat` OPENCLAW_ prefix patch still pending (ACTIVE_TASKS #6) — Windows/local only, N/A in this container.
- This is a **remote container**: no Windows scheduled tasks, no Ollama, no `.env`. Anything "running" referenced
  in old handoffs refers to Ronnie's local machine, not here.
</content>
