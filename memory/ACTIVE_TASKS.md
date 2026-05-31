# Active Tasks

## HIGH PRIORITY

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

### 6. Patch `infra/sync_to_vault.bat` to OPENCLAW_ prefix convention
- **Status:** Hands-off notice received 2026-05-31 ~06:55 — another Claude Code session is reorganizing `Documents/Obsidian Vault/`. Don't touch the vault until cleared.
- **What I did:** synced `memory/` to vault before the notice → un-prefixed duplicates of ACTIVE_TASKS/DECISIONS/SESSION_HANDOFF now sit alongside the prior reorg's `OPENCLAW_*.md` versions in `20 - OpenClaw/Memory/`. Reverted my un-committed `sync_to_vault.bat` naming patch.
- **Steps after all-clear:**
  1. See what naming convention the reorg session standardized on
  2. Patch `infra/sync_to_vault.bat` to match (likely the `OPENCLAW_` prefix per prior commit `89b8ee2`)
  3. Delete the un-prefixed duplicates I left in the vault if no longer wanted
  4. Re-run `infra/sync_to_vault.bat`
  5. From the vault: stage `20 - OpenClaw/Memory/` only, commit with `clawbot@openclaw.local`, push to `origin/main`

## DEFERRED INDEFINITELY

### 7. Ruflo skill installation
- **Status:** No `SKILL.md` on disk in any expected location
- **Why deferred:** Lower priority than auth + daily routine; per-prompt hardcoded rules cover for now
- **Action when ready:** Save Ruflo template to `skills/ruflo/SKILL.md`, install in Claude Code skills dir
