# Active Tasks

## HIGH PRIORITY

### 1. Refresh Crypto.com API key
- **Status:** Blocking live operations and Phase 1+2 of `next_session` workflow
- **Why deferred:** Required manual step (browser, Crypto.com UI), can't be automated
- **Steps when ready:**
  1. `crypto.com/exchange` → Settings → API Keys
  2. DELETE the old key (revoke, don't reuse)
  3. CREATE NEW key with READ + TRADE only (NEVER WITHDRAW)
  4. Paste both API_KEY and SECRET into `.env` (NOT `.env.new` — that file is stale)
  5. Run: `python -m infra.verify_cryptocom_auth`
  6. If 200 OK: update `STARTING_BALANCE_USD` in `.env` to the printed real balance

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

### 4. Day-14 LiquiditySweep paper-watch final decision — 2026-06-14
- **Action:** Compare live signals to backtest expectations (see `memory/strategy/paper-watch-liquiditysweep.md` for criteria)
- **Decision:** Wire as Category B / extend / retire strategy / try ensemble

## BLOCKED — WAITING ON EXTERNAL

### 5. Resume vault sync + commit + push (after vault reorg all-clear)
- **Status:** Hands-off notice received 2026-05-31 ~06:55 — another Claude Code session is reorganizing `Documents/Obsidian Vault/`. Don't touch the vault until cleared.
- **What I did:** synced `memory/` to vault before the notice → un-prefixed duplicates of ACTIVE_TASKS/DECISIONS/SESSION_HANDOFF now sit alongside the prior reorg's `OPENCLAW_*.md` versions in `20 - OpenClaw/Memory/`. Reverted my un-committed `sync_to_vault.bat` naming patch.
- **Steps after all-clear:**
  1. See what naming convention the reorg session standardized on
  2. Patch `infra/sync_to_vault.bat` to match (likely the `OPENCLAW_` prefix per prior commit `89b8ee2`)
  3. Delete the un-prefixed duplicates I left in the vault if no longer wanted
  4. Re-run `infra/sync_to_vault.bat`
  5. From the vault: stage `20 - OpenClaw/Memory/` only, commit with `clawbot@openclaw.local`, push to `origin/main`

## DEFERRED INDEFINITELY

### 6. Ruflo skill installation
- **Status:** No `SKILL.md` on disk in any expected location
- **Why deferred:** Lower priority than auth + daily routine; per-prompt hardcoded rules cover for now
- **Action when ready:** Save Ruflo template to `skills/ruflo/SKILL.md`, install in Claude Code skills dir
