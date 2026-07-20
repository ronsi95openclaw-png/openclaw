# Active Tasks

## HIGH PRIORITY

### 0. Add paper-watch calendar reminders to phone (DUE: before 2026-06-07)
- **Status:** Deferred during session_close PART A — "will add later" per Ronnie
- **Risk:** If not added before 2026-06-07, the Day-7 peek + Day-14 decision can drift and the 14-day paper-watch experiment loses its capture point
- **Reminders to add:**
  - 📅 Sunday 2026-06-07 09:00 — "LiquiditySweep paper-watch — Day 7 peek"
  - 📅 Sunday 2026-06-14 09:00 — "LiquiditySweep paper-watch — Day 14 DECISION"
- **Why phone, not vault:** lock-screen notification at 9am Sunday vs. a markdown file that gets missed

### 1. Before any LIVE-mode flip: verify `private/create-order` on v2
- **Status:** Open — gates any future TRADING_MODE=LIVE switch
- **Why:** Bot's only un-verified private endpoint after the v1→v2 migration. The URL is patched to v2 (executor.py:22) and uses the same `_sign` function that's been proven on v2, but we couldn't safely test it without placing a real order.
- **Steps when ready (next session, NOT this one):**
  1. Confirm `TRADING_MODE=DEMO` is still set; backup `.env`
  2. Use Python REPL: `python -c "from dotenv import load_dotenv; load_dotenv(); from trading.executor import _place_order; print(_place_order('XRP_USDT', 'BUY', 3.0))"` (small notional, low-priced coin)
  3. Check response: code 0 → working; non-zero or HTTP error → document and revert
  4. If order filled, sell to flat via the Crypto.com dashboard (don't add new code paths)
  5. Log the verification result to memory/CHANGES.md
  6. Only after a successful create-order test should LIVE mode be considered

### 2. ~~Refresh Crypto.com API key~~ — DONE 2026-05-31
- Keys swapped from `.env.new` into `.env` (backed up to `.env.backup-balance-update-*`)
- Verifier returns 200 OK on v2; balance $96.39 USD
- STARTING_BALANCE_USD updated 96.00 → 96.39

### 2. Build DAILY_ROUTINE.md adapted to real paths
- **Status:** Template lives in conversation history; no file on disk
- **Why deferred:** Must ADAPT to actual paths (root `memory/`, not template's `02_CRYPTOBOT/memory/`); pasting blind would re-introduce the RONSI95-template drift
- **Steps when ready:**
  1. Open Claude Code in the Claude-openclaw root
  2. Paste the daily-routine template
  3. Instruct: "adapt to the real paths in this repo, don't paste blindly"
  4. Save to `memory/DAILY_ROUTINE.md`
  5. Reference the existing `infra/verify_cryptocom_auth.py` as the auth check

## MEDIUM PRIORITY

### 3. Day-7 LiquiditySweep paper-watch review — 2026-06-07
- **Action:** Read `data/paper_watch/liquidity_sweep.jsonl`, count HIGH-confidence signals, classify market regime
- **Decision:** Continue / extend / kill paper-watch
- **Manual run check:** `python -m infra.paper_watch_liquiditysweep` from the Claude-openclaw root

### 5. Day-14 LiquiditySweep paper-watch final decision — 2026-06-14
- **Action:** Compare live signals to backtest expectations (see `memory/strategy/paper-watch-liquiditysweep.md` for criteria)
- **Decision:** Wire as Category B / extend / retire strategy / try ensemble

## BLOCKED — WAITING ON EXTERNAL

### 6. ~~Patch `infra/sync_to_vault.bat` to OPENCLAW_ prefix convention~~ — DONE 2026-06-27
- **Status:** ✅ Complete — `infra/sync_to_vault.bat` rewritten on branch `claude/graphify-ruflo-obsidian-0ebgmd`
- **What changed:**
  - Old `:sync` used bare `xcopy` (no prefix)
  - New `:sync` uses a FOR loop: `ACTIVE_TASKS.md` → `OPENCLAW_ACTIVE_TASKS.md`, etc.
  - `trash_hauling_bot/memory/*.md` → `HAULYALL_*.md` in vault `10 - HaulYA'LL!`
  - `memory/*.md` → `OPENCLAW_*.md` in vault `20 - OpenClaw/Memory`
  - `memory/strategy/*.md` → `OPENCLAW_*.md` in vault `20 - OpenClaw/Memory/Strategy`
  - `graphify-out/GRAPH_REPORT.md` → `OPENCLAW_GRAPH_REPORT.md` in vault `20 - OpenClaw/Knowledge-Graph` ← NEW
- **Still needed (manual, on Windows):**
  1. Run `infra/sync_to_vault.bat` once to push the prefixed files to vault
  2. Delete old un-prefixed duplicates in `20 - OpenClaw/Memory/` (ACTIVE_TASKS.md, DECISIONS.md, SESSION_HANDOFF.md)
  3. From vault: stage `20 - OpenClaw/` only, commit with `clawbot@openclaw.local`, push `origin/main`

## DEFERRED INDEFINITELY

### 7. ~~Ruflo skill installation~~ — DONE 2026-06-27
- **Status:** ✅ Complete — `skills/ruflo/SKILL.md` created on branch `claude/graphify-ruflo-obsidian-0ebgmd`
- **Load path:** `skills/ruflo/SKILL.md` (Windows: `C:\Users\ronsi95openclaw\Claude-openclaw\skills\ruflo\SKILL.md`)
- **Alt install:** copy to `%APPDATA%\Claude\skills\ruflo\SKILL.md` for global availability
- **What's in it:** universal session rules, escalation hierarchy, memory paths, Hermes integration, session-end checklist

### 8. Hermes knowledge-graph agent — DONE 2026-06-27
- **Status:** ✅ Complete — `agents/hermes.py` + daily APScheduler job + `/hermes` Telegram command
- **Enable:** `/hermes on` in Telegram (runs at 09:30 UTC daily)
- **Manual:** `/hermes now` to trigger immediately
- **Outputs:** `graphify-out/` (git-ignored) + `memory/HERMES_GRAPH_REPORT.md` (synced to vault via sync_to_vault.bat)
- **Obsidian:** `graphify-out/obsidian/` — copy to vault `25 - AI/Knowledge-Graph/` after sync_to_vault.bat patch

### 9. Repo-native daily-routine skill + review subagents — DONE 2026-07-11 (Fable audit)
- **Status:** ✅ Complete — added `.claude/skills/daily-routine/SKILL.md` (wraps this file + `memory/DAILY_ROUTINE.md`) plus `.claude/agents/trading-risk-reviewer.md` and `.claude/agents/security-auditor.md` proactive review subagents
- **Note:** this branch was cut before #7 landed, so its own changelog entry (`memory/CHANGES.md`, 2026-07-11) describes itself as "closing" a still-deferred Ruflo skill — that premise was stale by the time it merged. Ruflo (#7) and this repo-native skill are separate and both now present; no conflict between them.
