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

## DEFERRED INDEFINITELY

### 5. Ruflo skill installation
- **Status:** No `SKILL.md` on disk in any expected location
- **Why deferred:** Lower priority than auth + daily routine; per-prompt hardcoded rules cover for now
- **Action when ready:** Save Ruflo template to `skills/ruflo/SKILL.md`, install in Claude Code skills dir
