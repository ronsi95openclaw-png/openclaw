# HAULYA'LL! — PROJECT STATE AUDIT

_Read-only inventory. Generated 2026-06-07. No project files changed (this report is the only new file)._

---

## TL;DR (the one thing to know)

**There is no standalone "HaulYA'LL!" project in this repo.** This repository is the
**OpenClaw / ClawBot crypto bot**. The only HaulYA'LL-related code is the
`trash_hauling_bot/` subdirectory (branded **"HaulYeah"** in code) — a runnable
Facebook-Marketplace lead-gen bot.

**None of the "built assets" from the handoff exist here:** no IG carousel, no animated
promo, no AI-video prompts, no website source, no Meta Ads config, no contact info, no
booking/payment code. The repo contains **zero `.html` files**. Those artifacts, if they
exist, live somewhere else (the Obsidian vault `35 - HaulYA'LL` folder, Manus, Drive, or a
separate machine) — **not in this repository.**

---

## Reality vs Handoff

| Handoff claim (task brief) | What's actually in this repo | Verdict |
|---|---|---|
| A "HaulYA'LL!" project directory | Only `trash_hauling_bot/` inside the crypto repo; brand in code is **"HaulYeah"** | ⚠️ DRIFT — name mismatch, no dedicated project root |
| 5-slide IG carousel (HTML) | Not present. `find -iname "*.html"` → **0 files** | ❌ MISSING from repo |
| 30s animated HTML promo | Not present | ❌ MISSING from repo |
| AI video-generator prompts | Not present | ❌ MISSING from repo |
| Contact info `(469) 618-7677`, `junkgone@haulya-ll.com`, Mon–Sat 7am–7pm, DFW | **No phone, email, hours, or DFW string anywhere in code.** Deliberately omitted per `DECISIONS.md` ("avoid baking in possibly-wrong contact details") | ❌ Cannot verify — nothing to check against |
| `haulyalltx.manus.space` site, local editable source | **No `manus` reference anywhere.** No site source in repo | ❌ Hosted-only, no local source here |
| trash_hauling_bot code + logs | Code present and coherent. **No `data/` dir** (logs are git-ignored runtime) | ✅ Code EXISTS / ⚪ logs not in repo |
| Playwright lead-gen behind a feature flag (OFF) | Playwright scraper exists; gated by **`DRY_RUN`** (default `false`), not a dedicated "lead-gen feature flag." Outreach is **queue + human-confirm, never auto-sends** | ⚠️ PARTIAL — human-in-loop confirmed; "flag OFF" ≈ `DRY_RUN` |
| Meta Ads acct `795836823772411` (read-only) | **No account id, no Meta Ads config anywhere in repo.** (An Ads MCP server is wired into this session's toolset, but I did **not** call it and the repo holds no reference) | ❌ No config in repo |
| Booking / payment / quoting | Quote estimator exists (`quote.py`); lead log via Sheets/Calendar. **No payment code, no customer self-booking flow** | ⚠️ PARTIAL (quote only) |
| HaulYeah is "deliberately isolated" from the crypto stack | **Coupled at repo + config level:** same git repo; `config.py` loads the crypto bot's root `../.env`. Token *values* are namespaced (`TRASH_BOT_*`, `FB_*`) so they can't cross-load, but the file and repo are shared | ⚠️ DRIFT — isolated in tokens, NOT in repo/config |

### Stale docs found (handoff has drifted from code)
- **`CLAUDE.md` (root)** says trash_hauling_bot has *"its own venv + `.env.haulyeah`"*. **Stale** — env was consolidated into root `../.env` (commit `df85ae1` "env consolidation"); `config.py` confirms `load_dotenv(_HERE.parent / ".env")`.
- **`trash_hauling_bot/memory/SESSION_HANDOFF.md` (2026-05-29)** says quote/review are *"NOT yet wired into OutreachAgent."* **Stale** — commit `2c2a404` (2026-05-30) wired them: `_maybe_append_quote()` gated by `OUTREACH_INCLUDE_QUOTE` (default off) + a `/review` Telegram command.

---

## Git State

- **Branch:** `claude/haulya-ll-state-audit-O3ky5`
- **Working tree:** clean (before this report).
- **Stashes:** none.
- **Last 10 commits** (all crypto/vault/memory — only the older 3 touch the bot):
  ```
  b4431d8 daily: first DAILY_ROUTINE.md run — validation pass + 2 adaptation fixes
  61daa63 docs(memory): session_close PART A wrap — push log + calendar deferral
  0dce709 docs(memory): rewrite SESSION_HANDOFF for end-of-2026-05-31 state
  d1c0149 docs(memory): post_vault_next workflow complete (PHASE 4 wrap)
  f297ab0 feat(memory): DAILY_ROUTINE.md adapted from v2.1 template
  70cb112 feat(exchange): migrate private API v1 -> v2 + USD parser fix
  2d2124e docs(memory): vault all-clear — STEP 7B resumed, 5d1d8a7 pushed
  dc03f9c docs(memory): log vault hands-off + STEP 7 deferred
  4444841 feat(paper-watch): LiquiditySweep daily signal logger + scheduled task
  f27a4aa feat(backtest): 5-strategy comparison + regime test + memory scaffold
  ```
- **trash_hauling_bot history** (only 3 commits ever touch it):
  ```
  2c2a404 feat: wire quote/review helpers, centralize version, allowlist warning
  28d47f9 chore: audit quick-wins — error handler, DRY_RUN guard, docs cleanup
  df85ae1 feat: watchdog, backoff, circuit breaker, /report, quotes, env consolidation
  ```

---

## What's Actually In `trash_hauling_bot/` (the real thing)

A coherent, test-backed Python bot — **runnable**, but unproven live:

- **`main.py`** — orchestrator: APScheduler loop (scraper every 30m, calendar sync every 5m, lead-aging daily) + Telegram bot.
- **`agents/scraper.py`** — FB Marketplace scraper via **Playwright** (async). Scores leads (urgency/size keywords), dedups, writes to Sheets. Requires a one-time `--login`. Skipped entirely when `DRY_RUN=true`.
- **`agents/outreach.py`** — generates messages (Claude or template), **queues them for a human to confirm** (`confirm_send` / `deny`); *"Never auto-sends."* `_maybe_append_quote()` optionally appends a price range.
- **`agents/quote.py`** — pure price-tier estimator (`$89 → $699`).
- **`agents/review.py`** — post-job Google-review request message (URL passed in).
- **`agents/calendar_sync.py`**, **`integrations/`** (`telegram_bot.py`, `gcal.py`, `sheets.py`).
- **`utils/`** — `sanitize`, `dedup`, `retry`, `audit`; includes prompt-injection guard on scraped text.
- **`tests/`** — pytest suite (handoff cites ~96 passing HaulYeah tests). **Not run** (read-only audit).
- **Runnable?** Yes, two ways: (1) `DRY_RUN=true` keyless smoke test; (2) live needs `TRASH_BOT_TOKEN`, `GOOGLE_SHEET_ID`, `FB_SEARCH_LOCATION`, `data/google_credentials.json`, and a saved FB session. **No `data/` dir present → no run logs in repo → no evidence it has ever run live.**

---

## Slice Map

| Slice | Status | Evidence (one line) |
|---|---|---|
| **1 — Brand + content live, no leak** | ❌ **MISSING** | No assets, no site source, no contact path anywhere in this repo (0 HTML, no phone/email/manus refs). |
| **2 — Manual quote → book → pay** | ⚠️ **PARTIAL** | `quote.py` estimator exists + Sheets/Calendar lead log; **no payment, no customer self-book** (calendar sync is for scraped leads, not customer-initiated booking). |
| **3 — Quoting bot** | ⚠️ **PARTIAL / EXISTS** | `trash_hauling_bot` is real and runs in `DRY_RUN`; quote wired but **default-off** (`OUTREACH_INCLUDE_QUOTE=false`). Unproven live (no logs, needs FB+Google creds). |
| **4 — Organic content automation** | ❌ **MISSING (here)** | Playwright is for **scraping leads**, not posting content. `INSTAGRAM_*`/`TIKTOK_*` env keys exist but no HaulYA'LL posting code in this repo. Human-in-loop preserved (outreach confirm). |
| **5 — Paid ads (Meta write)** | ❌ **MISSING (here)** | No Meta Ads code/config/account id in repo. Ads MCP server is present in the session toolset but **was not called**; nothing wires it to HaulYA'LL. |

---

## Biggest Gaps / What's Blocking Slice 1

1. **The assets aren't in this repo.** Carousel, promo, and video prompts are referenced by the vault (`35 - HaulYA'LL`) but physically absent here. Slice 1 can't be "export-ready" from this repo because there's nothing to export.
2. **No website source.** `haulyalltx.manus.space` appears Manus-hosted with no local, editable copy in the repo — so click-to-text/call, hours, and service-area can't be verified or edited from here.
3. **No contact info to verify.** `(469) 618-7677`, `junkgone@haulya-ll.com`, Mon–Sat 7am–7pm, DFW appear **nowhere** in code (omitted on purpose). Can't flag stale vs. correct because there's no instance.
4. **Brand inconsistency.** Code says **"HaulYeah"**; handoff/vault/domain say **"HaulYA'LL!" / haulya-ll.com**. This must be settled before any public-facing content ships.
5. **Isolation is partial.** The bot shares the crypto repo and the crypto bot's root `.env` file. Token values are namespaced (safe from cross-loading), but the "deliberately isolated" claim overstates reality at the repo/config layer.

---

## Open Questions for Ronnie

1. **Where do the built assets actually live?** They're not in this repo — vault `35 - HaulYA'LL`, Manus, Google Drive, or a local machine folder? Point me at the real source before anyone calls Slice 1 "done."
2. **Is the Manus site editable anywhere, or hosted-only?** If there's no exportable source, edits (contact, hours, CTA) have to happen in Manus's UI, not in code.
3. **Canonical brand: "HaulYeah" or "HaulYA'LL!"?** Code, vault, and domain disagree. Which one wins?
4. **Has the bot ever run live?** No `data/` logs exist in the repo (git-ignored). Are there logs on the real machine showing a last run / last error, or is this still strictly `DRY_RUN`?
5. **Meta Ads (`795836823772411`):** configured only via the MCP server (outside the repo), or is there a config file I should be pointed to? I did **not** call the Ads MCP.
6. **Booking + payment:** handoff says none in place — **confirmed**. Is Slice 2 expected to be Google-Sheets/Calendar-manual for now, or is a real booking/payment integration the next build?

---

_Audit method: filesystem + git read-only. No bot run, no Playwright, no network/MCP calls, no edits to any project file. Crypto/vault/trading stack not inspected beyond confirming the coupling boundary._
