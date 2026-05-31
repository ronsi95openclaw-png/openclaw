# CRYPTOBOT — POST-BACKTEST COMMIT & NEXT STEPS
## Claude Code Prompt | Ronsi95 AI OS | May 2026
## Save as: C:\Users\ronsi95openclaw\Claude-openclaw\workflows\post_backtest.md

> **HOW TO USE:**
> Paste this entire file into Claude Code as your next message. It does a
> pre-commit sanity check, commits the backtest session locally (no push),
> and sets up the LiquiditySweep paper-watch + auth fix for next session.

---

## 🧠 CONTEXT

Last session: Backtest workflow completed. Key outcomes:
- 5-strategy × 4-symbol comparison on 1d candles run
- BTC 4-quarter regime test: **NO strategy hit 3/4 positive quarters**
- LiquiditySweep flagged as paper-watch candidate (1/4 but +$0.777 PnL, best per-symbol win rates)
- Executor NOT wired (Phase 5D escalation respected — discipline held)
- Memory scaffold built at repo root + synced to Obsidian
- `verify_cryptocom_auth.py` Unicode bug fixed
- `.env.new` exists but CRYPTOCOM keys still broken (deferred)
- No `DAILY_ROUTINE.md` on disk yet (template-vs-reality gap — deferred)

Claude Code proposed a commit. This prompt validates it, executes it locally,
and lines up the next moves correctly.

---

## ⚠️ ABSOLUTE RULES

1. Load Ruflo first (Step 0)
2. **LOCAL commit only — NEVER push** without explicit "yes push"
3. Never stage `.env`, `.env.new`, `.env.backup-*`, `.env.old-*`, or runtime data (`.json` state files, `.log` files)
4. Never wire any strategy into the executor in this session
5. Never flip TRADING_MODE
6. If the pre-commit sanity check finds anything sketchy → STOP and escalate

---

## STEP 0 — LOAD RUFLO + READ CONTINUITY

```powershell
$root = "C:\Users\ronsi95openclaw\Claude-openclaw"

# Load Ruflo (try common paths)
$rufloPaths = @(
    "$root\04_SKILLS\ruflo\SKILL.md",
    "$env:APPDATA\Claude\skills\ruflo\SKILL.md",
    "$root\skills\ruflo\SKILL.md"
)
$ruflo = $null
foreach ($p in $rufloPaths) {
    if (Test-Path $p) { $ruflo = $p; break }
}

if ($ruflo) {
    Get-Content $ruflo
    Write-Host "`n[Ruflo loaded — applying universal rules to this session]"
} else {
    Write-Host "⚠️ Ruflo skill not found on disk — flagging as ACTIVE_TASK for next session"
    Write-Host "Continuing with hard-coded rules from this prompt only"
}

# Read last session handoff
Write-Host "`n=== LAST SESSION HANDOFF ==="
Get-Content "$root\memory\SESSION_HANDOFF.md" -ErrorAction SilentlyContinue

Write-Host "`n=== ACTIVE TASKS ==="
Get-Content "$root\memory\ACTIVE_TASKS.md" -ErrorAction SilentlyContinue

Write-Host "`n=== RECENT CHANGES ==="
Get-Content "$root\memory\CHANGES.md" -Tail 20 -ErrorAction SilentlyContinue
```

State out loud:
- We're committing yesterday's backtest work locally
- The Phase 5D escalation (no strategy passed regime test) is the BIG finding
- LiquiditySweep is the paper-watch candidate, NOT a wiring decision
- Two items still deferred: CRYPTOCOM key refresh, DAILY_ROUTINE.md build

---

## STEP 1 — PRE-COMMIT SANITY CHECK

Before staging anything, verify the working tree is clean of dangerous content.

### Step 1A: Verify `.env`-family files are NOT staged
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw

Write-Host "=== Checking .env files are gitignored ==="
git check-ignore .env
git check-ignore .env.new
git check-ignore .env.backup-20260530-131338
git check-ignore .env.old-20260530-134625

# Each should print the filename (meaning IGNORED). If anything is silent → bug.
```

If ANY .env-family file is NOT ignored → STOP. Print:
```
🚨 CRITICAL: An .env file is not in .gitignore.
This must be fixed before any commit. Manual review required.
```
Halt the workflow.

### Step 1B: Inspect what the proposed commit will touch
```powershell
git status

Write-Host "`n=== Files in memory/ to be staged ==="
git status memory/ --short

Write-Host "`n=== Sanity check: only .md files in memory/? ==="
$nonMd = Get-ChildItem memory -Recurse -File | Where-Object { $_.Extension -ne ".md" -and $_.Name -notmatch "^\." }
if ($nonMd) {
    Write-Host "⚠️ Non-markdown files in memory/:"
    $nonMd | Select-Object FullName, Length, LastWriteTime | Format-List
} else {
    Write-Host "✅ memory/ contains only markdown files"
}

Write-Host "`n=== Check for runtime state accidentally tracked ==="
git status --short | Select-String "\.json|\.log|trades\.|state\.|trading_mode"
```

If `memory/` has anything other than `.md` files OR git status shows runtime
state files being added → STOP. Print what was found, ask Ronnie what to do.

### Step 1C: Verify the actual files match Claude Code's claim
```powershell
Write-Host "`n=== Modified files ==="
git diff --name-only

Write-Host "`n=== Untracked files (new) ==="
git ls-files --others --exclude-standard

# Expected from Claude Code's proposal:
# Modified:   infra/sync_to_vault.bat
# Modified:   infra/verify_cryptocom_auth.py
# Untracked:  infra/run_strategy_comparison.py
# Untracked:  memory/ (and its contents)
```

If anything UNEXPECTED shows up (other files modified, other untracked dirs) →
print them and ask before staging.

### Step 1D: Confirm `.env.new` and CRYPTOCOM keys
```powershell
# .env.new exists?
Test-Path .env.new

# Verify .env.new is also gitignored (it should be — same pattern as .env)
git check-ignore .env.new

# Compare CRYPTOCOM key presence between .env and .env.new (NEVER read values)
python -c "
from dotenv import dotenv_values
old = dotenv_values('.env')
new = dotenv_values('.env.new')
print('=== .env CRYPTOCOM_API_KEY length:', len(old.get('CRYPTOCOM_API_KEY','')))
print('=== .env CRYPTOCOM_SECRET length:', len(old.get('CRYPTOCOM_SECRET','')))
print('=== .env.new CRYPTOCOM_API_KEY length:', len(new.get('CRYPTOCOM_API_KEY','')))
print('=== .env.new CRYPTOCOM_SECRET length:', len(new.get('CRYPTOCOM_SECRET','')))
same_key = old.get('CRYPTOCOM_API_KEY') == new.get('CRYPTOCOM_API_KEY')
same_secret = old.get('CRYPTOCOM_SECRET') == new.get('CRYPTOCOM_SECRET')
print('=== Keys identical between .env and .env.new:', same_key and same_secret)
"
```

If both files have identical CRYPTOCOM values → confirms the 401 will still
fail. Log this as an ACTIVE_TASK so it's not lost.

**→ CHECKPOINT 1: All sanity checks pass before proceeding.**

---

## STEP 2 — STAGE & COMMIT (LOCAL ONLY)

### Step 2A: Stage the specific files (explicit, not `git add .`)
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw

git add infra/sync_to_vault.bat
git add infra/verify_cryptocom_auth.py
git add infra/run_strategy_comparison.py
git add memory/
```

### Step 2B: Verify the stage
```powershell
git status --short
git diff --cached --stat
```

Expected output: 4-ish files staged, all aligned with what Claude Code proposed.
Nothing in `.env*`, nothing in `data/` runtime state.

### Step 2C: Commit (local only)
```powershell
git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" commit -m "feat(backtest): 5-strategy comparison + regime test + memory scaffold

- Built infra/run_strategy_comparison.py: 4 candidates + RSI+MACD baseline
  against 1d candle data (~299 days) with 4-quarter BTC regime test
- No strategy hit the 3/4 positive-quarters bar; LiquiditySweep is the
  paper-watch candidate (1/4, but +\$0.777 PnL and strongest per-symbol
  win rates). Executor untouched per Phase 5D escalation.
- Memory scaffold (CHANGES, DECISIONS, SESSION_HANDOFF, ACTIVE_TASKS,
  strategy/) bootstrapped at repo root; sync_to_vault.bat extended to
  mirror it into Obsidian's 20 - OpenClaw/Memory.
- Fix Unicode crash in infra/verify_cryptocom_auth.py (Windows cp1252
  console couldn't render emoji before the 401 diagnostic printed).

Phase 5D escalation: regime test prevented overfit wiring.
Deferred to next session:
  - CRYPTOCOM_API_KEY refresh (verifier still returns 401)
  - DAILY_ROUTINE.md build (no template on disk yet)

Co-Authored-By: Claude Opus 4 <noreply@anthropic.com>"
```

Use repo-local identity (not global) — same pattern as the vault commit.

### Step 2D: Verify the commit landed
```powershell
git log -1 --stat
git branch -v
```

Print the commit SHA — Ronnie will want it for reference.

**Do NOT push.** If Ronnie says "yes push" later, the command is:
```powershell
# Only run this if Ronnie explicitly says "yes push"
# git push origin feature/telegram-notifications
```

---

## STEP 3 — LOG TO CHANGES.MD

Append to `memory/CHANGES.md`:
```markdown
## [YYYY-MM-DD HH:MM] — A — Backtest session committed locally
**Trigger:** End-of-session, committing the backtest workflow output
**Action:** Staged 4 files (infra changes + memory scaffold + comparison runner), committed with repo-local ClawBot identity
**Result:** Local commit [SHA] on feature/telegram-notifications. NOT pushed.
**Files touched:**
  - infra/sync_to_vault.bat (M)
  - infra/verify_cryptocom_auth.py (M)
  - infra/run_strategy_comparison.py (NEW)
  - memory/ (NEW scaffold)
**Git tag:** None (not creating tags for routine commits)
**Approved by:** Ronnie ("commit it")
**Status:** APPLIED — LOCAL ONLY
---
```

---

## STEP 4 — SET UP NEXT SESSION (LiquiditySweep Paper-Watch)

The Phase 5D finding means we don't wire anything. But we DO start observing
LiquiditySweep's signals against live market data so we can compare to the
backtest in 2 weeks.

### Step 4A: Create a paper-watch tracking note
Create `memory/strategy/paper-watch-liquiditysweep.md`:

```markdown
---
title: LiquiditySweep Paper-Watch — Started [today]
strategy: LiquiditySweep
mode: paper-watch (signal-logging only, NO wiring)
start_date: [today]
review_date: [today + 14 days]
backtest_baseline: 1/4 positive quarters, +$0.777 PnL (data: 299 days, 1d candles, 4 symbols)
---

# LiquiditySweep Paper-Watch

## What this is
Two weeks of OBSERVING LiquiditySweep signals against live Crypto.com data
WITHOUT wiring it into the executor. Goal: see if the strategy's behavior on
live market regime matches the backtest, or diverges.

## What we are NOT doing
- ❌ Wiring LiquiditySweep into trading/executor.py
- ❌ Trading any signals it generates (paper or live)
- ❌ Adding capital
- ❌ Switching off DEMO mode

## What we ARE doing
- ✅ Logging every LiquiditySweep signal to data/paper_watch/liquidity_sweep.jsonl
- ✅ Recording: timestamp, symbol, signal direction, would-be entry price
- ✅ Tracking what would have happened (P&L if the trade had been taken)
- ✅ Comparing to the backtest's 1/4 positive-quarter performance

## Success criteria for considering wiring (after 14 days)
- Live signal frequency within ±30% of backtest signal frequency
- Live win rate within ±15% of backtest win rate
- No catastrophic single-period loss (>5% would-be drawdown)
- Market regime classified (trending / ranging) — verify strategy works in current regime

## Review schedule
- Day 7: Mid-point check, document regime + early divergence
- Day 14: Full review, decision on wiring

## If at Day 14
- ✅ Criteria met → propose wiring as Category B in next session
- ⚠️ Criteria partially met → extend paper-watch 2 more weeks
- ❌ Criteria failed → strategy retired, return to backtest selection
```

### Step 4B: Build the signal logger
Create `infra/paper_watch_liquiditysweep.py`:

```python
"""
paper_watch_liquiditysweep.py — Logs LiquiditySweep signals against live Crypto.com
data WITHOUT executing any trades.

Runs as a scheduled task every 4 hours (matches the candle close cadence).
Writes one JSONL entry per signal evaluation to:
  data/paper_watch/liquidity_sweep.jsonl

NEVER places an order. NEVER touches the executor.
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

from trading.strategies import liquidity_sweep

SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]
OUTPUT = Path(__file__).parent.parent / "data" / "paper_watch" / "liquidity_sweep.jsonl"
TIMEFRAME = "1D"  # matches the 1d backtest decision
CANDLE_COUNT = 100  # enough for warmup

def fetch_recent_candles(symbol):
    """Public Crypto.com endpoint — no auth required."""
    url = (
        f"https://api.crypto.com/v2/public/get-candlestick"
        f"?instrument_name={symbol}&timeframe={TIMEFRAME}&count={CANDLE_COUNT}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("result", {}).get("data", [])
    except Exception as e:
        return None

def evaluate_one(symbol):
    candles = fetch_recent_candles(symbol)
    if not candles:
        return {"symbol": symbol, "error": "fetch failed"}

    closes = [float(c["c"]) for c in candles]
    if len(closes) < liquidity_sweep.warmup:
        return {"symbol": symbol, "error": "not enough warmup data"}

    try:
        signal = liquidity_sweep.evaluate(symbol, closes)
        return {
            "ts": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "current_price": closes[-1],
            "signal": signal._asdict() if hasattr(signal, '_asdict') else signal.__dict__ if signal else None,
        }
    except Exception as e:
        return {"symbol": symbol, "error": f"strategy error: {e}"}

def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("a") as f:
        for symbol in SYMBOLS:
            result = evaluate_one(symbol)
            f.write(json.dumps(result) + "\n")
            print(f"  {symbol}: {result.get('signal') or result.get('error')}")

if __name__ == "__main__":
    main()
```

### Step 4C: Install as scheduled task (every 4 hours)
```powershell
schtasks /create /tn "ClawBot-LiquiditySweep-Watch" `
  /tr "python C:\Users\ronsi95openclaw\Claude-openclaw\infra\paper_watch_liquiditysweep.py" `
  /sc HOURLY /mo 4 /f

Write-Host "Paper-watch scheduled every 4 hours"
schtasks /query /tn "ClawBot-LiquiditySweep-Watch" /fo LIST
```

Run once immediately to verify it works:
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
python infra\paper_watch_liquiditysweep.py
Get-Content data\paper_watch\liquidity_sweep.jsonl -Tail 4
```

### Step 4D: Log it
```markdown
## [YYYY-MM-DD HH:MM] — A — LiquiditySweep paper-watch started (no executor changes)
**Trigger:** Phase 5D escalation — no backtest winner, but LiquiditySweep flagged for observation
**Action:** Built infra/paper_watch_liquiditysweep.py + scheduled task (4hr cadence). Pure read-only signal logging.
**Result:** Signals now logged to data/paper_watch/liquidity_sweep.jsonl. Executor untouched.
**Files touched:** infra/paper_watch_liquiditysweep.py (new), memory/strategy/paper-watch-liquiditysweep.md (new)
**Approved by:** Auto (Category A — observation only, no trading impact)
**Status:** APPLIED
---
```

---

## STEP 5 — UPDATE ACTIVE_TASKS.MD WITH THE DEFERRED ITEMS

The two deferred items must NOT get lost. Update `memory/ACTIVE_TASKS.md`:

```markdown
# Active Tasks

## HIGH PRIORITY

### 1. Refresh Crypto.com API key
- **Status:** Blocking live operations + Phase 1+2 of next_session workflow
- **Why deferred:** Required manual step (Crypto.com web UI), no progress without it
- **Steps when ready:**
  1. crypto.com/exchange → Settings → API Keys
  2. DELETE old key
  3. CREATE NEW key with READ + TRADE only (NEVER WITHDRAW)
  4. Paste both API_KEY and SECRET into .env (NOT .env.new — that file's stale)
  5. Run: `python -m infra.verify_cryptocom_auth`
  6. If 200 OK: update STARTING_BALANCE_USD in .env to the printed real balance

### 2. Build DAILY_ROUTINE.md from template v2.1
- **Status:** Template exists in Claude conversations but not on disk
- **Why deferred:** Should be ADAPTED to real paths, not pasted blindly
- **Path to use:** memory/ at repo root (not the template's 02_CRYPTOBOT/memory/)
- **Source:** Use the v2.1 daily routine spec from Claude Opus 4 session
- **Steps when ready:**
  1. Open Claude Code in this folder
  2. Paste the daily routine v2.1 template
  3. Tell it: "adapt this to the real folder paths in this repo, don't paste blindly"
  4. Save adapted version to memory/DAILY_ROUTINE.md

## MEDIUM PRIORITY

### 3. Day-7 LiquiditySweep paper-watch review
- **Date:** [today + 7 days]
- **Action:** Read data/paper_watch/liquidity_sweep.jsonl, count signals, classify regime
- **Decision:** Continue / extend / kill paper-watch

### 4. Day-14 LiquiditySweep paper-watch final decision
- **Date:** [today + 14 days]
- **Action:** Compare live signals to backtest, decide on wiring (Category B proposal)

## DEFERRED INDEFINITELY

### 5. Ruflo skill installation
- **Status:** No SKILL.md on disk in any expected location
- **Why deferred:** Lower priority than auth + daily routine; the rules are hard-coded into individual prompts for now
- **Action when ready:** Save Ruflo template to skills/ruflo/SKILL.md, install in Claude Code skills dir
```

---

## STEP 6 — UPDATE SESSION_HANDOFF.MD

```powershell
# Read current state of all the things we touched
$root = "C:\Users\ronsi95openclaw\Claude-openclaw"
$lastCommit = git -C $root log -1 --format="%h %s"
$watchTaskStatus = schtasks /query /tn "ClawBot-LiquiditySweep-Watch" /fo LIST | Select-String "Status"
$watchEntriesCount = (Get-Content "$root\data\paper_watch\liquidity_sweep.jsonl" -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
```

Write to `memory/SESSION_HANDOFF.md`:
```markdown
# Session Handoff — [today]

## What Was Accomplished
- Pre-commit sanity check on backtest session work (no .env contamination)
- Committed [LOCAL ONLY]: feat(backtest) 5-strategy comparison + memory scaffold + Unicode fix
- Local commit SHA: [from git log -1]
- LiquiditySweep paper-watch started (4hr cadence, signal-logging only, NO executor wiring)
- ACTIVE_TASKS.md updated with two HIGH priority deferrals (auth + daily routine)

## Current State
- Mode: DEMO (unchanged)
- Branch: feature/telegram-notifications (local commit, NOT pushed)
- Paper-watch task: scheduled, running every 4 hours
- Paper-watch entries logged: [count]
- Auth: STILL 401 (deferred)
- Real balance: STILL unknown (depends on auth fix)
- Strategy executor: STILL using dormant RSI+MACD (untouched, correct)

## What's Running
- ClawBot v0.9 in DEMO (PID from last session if still alive)
- HaulYeah in DRY_RUN
- Watchdog (every 5 min)
- NEW: LiquiditySweep paper-watch (every 4 hours)

## Next Session Priorities
1. Refresh Crypto.com API key (Active Task #1)
2. Verify auth, update STARTING_BALANCE_USD
3. Build DAILY_ROUTINE.md adapted to real paths
4. Day-7 paper-watch review (or sooner if you want a peek)

## Did NOT Do (intentional)
- Did not push to GitHub
- Did not wire LiquiditySweep into the executor
- Did not flip to LIVE mode
- Did not modify .env values

## Memory Block for Next Session
Project: CryptoBot
Last session: [today] — committed backtest + started paper-watch
Resume from: Refresh Crypto.com API key, then verify + update balance
Do not repeat: Wiring decisions (paper-watch must run 14 days first)
Open tasks: see memory/ACTIVE_TASKS.md (5 items, 2 HIGH)
```

---

## STEP 7 — SYNC TO VAULT & FINAL REPORT

### Step 7A: Sync vault
```powershell
& "C:\Users\ronsi95openclaw\Claude-openclaw\infra\sync_to_vault.bat"
```

Verify the new files appeared in `20 - OpenClaw\Memory\`:
```powershell
$vault = "$env:USERPROFILE\Documents\Obsidian Vault\20 - OpenClaw\Memory"
Get-ChildItem $vault -Recurse | Select-Object FullName, LastWriteTime | Format-Table
```

### Step 7B: Commit vault changes (separate repo)
```powershell
cd "$env:USERPROFILE\Documents\Obsidian Vault"
git status

# Only stage what we just synced
git add "20 - OpenClaw/Memory/"
git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" commit -m "vault: post-backtest commit + LiquiditySweep paper-watch started

- Backtest session committed in openclaw repo (local, not pushed)
- Phase 5D held: no strategy hit 3/4 regime bar
- LiquiditySweep paper-watch scheduled for 14-day observation
- Two ACTIVE_TASKS deferred: CRYPTOCOM key refresh, DAILY_ROUTINE.md build"

# Push the vault (this is YOUR vault, you push your own notes)
git push origin main 2>&1 | tail -3
```

### Step 7C: Print the final report
```
═══════════════════════════════════════════════════════════════════
  POST-BACKTEST WORKFLOW — COMPLETE
═══════════════════════════════════════════════════════════════════

LOCAL COMMIT MADE:    ✅ feature/telegram-notifications (local only)
COMMIT SHA:           [from git log -1]
PUSHED TO GITHUB:     ❌ (waiting on your "yes push")

NEW THIS SESSION:
  ✅ Paper-watch scheduled task (every 4hr)
  ✅ infra/paper_watch_liquiditysweep.py
  ✅ memory/strategy/paper-watch-liquiditysweep.md (review schedule)
  ✅ ACTIVE_TASKS.md updated with deferred items
  ✅ SESSION_HANDOFF.md updated
  ✅ Vault synced + committed + pushed

DELIBERATELY NOT DONE:
  ❌ Wired LiquiditySweep into executor (must observe 14 days first)
  ❌ Pushed feature/telegram-notifications (your call)
  ❌ Flipped to LIVE mode
  ❌ Refreshed CRYPTOCOM key (manual step in your hands)
  ❌ Built DAILY_ROUTINE.md (deferred to next session for adaptation)

NEXT SESSION COMMANDS:
  1. Refresh Crypto.com key (manual, in browser)
  2. In Claude Code: paste "verify auth and update balance"
  3. Build DAILY_ROUTINE.md adapted to real paths

PAPER-WATCH:
  Reviewing on: [today + 7 days]  (mid-point peek)
  Decision on:  [today + 14 days]  (wire or not?)
  Data at:      data/paper_watch/liquidity_sweep.jsonl
═══════════════════════════════════════════════════════════════════
```

### Step 7D: Telegram summary (only if wired and Ronnie confirms)
Print this and wait:
```
Want me to send a Telegram summary?
(I won't try without confirmation — last session noted Telegram is wired
 but I want to confirm before sending after a long session.)

Type 'yes telegram' to send, or skip.
```

If yes:
```python
from core.telegram_bot import alert
alert(
    "*✅ Post-Backtest Session*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Committed: backtest + memory scaffold (local)\n"
    "SHA: [from git log -1]\n"
    "Paper-watch: scheduled (4hr cadence)\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Mode: DEMO (still)\n"
    "Auth: 401 (deferred)\n"
    "Next: Crypto.com key refresh\n"
    "Review paper-watch: [today + 14 days]"
)
```

---

## 📌 WHAT THIS PROMPT WILL NEVER DO

- Push to GitHub without "yes push"
- Stage `.env`, `.env.new`, `.env.backup-*`, `.env.old-*`
- Stage runtime state (`.json`, `.log` files)
- Wire any strategy into the executor
- Flip TRADING_MODE
- Read .env VALUES
- Skip the sanity check before commit
- Use `git add .` (always explicit file paths)
- Send Telegram without confirmation
- Modify global git config (always use `-c user.name -c user.email` inline)

---

## ⏱️ EXPECTED TIMING

```
STEP 0  →  1 min   (load Ruflo, read continuity)
STEP 1  →  2 min   (pre-commit sanity check)
STEP 2  →  1 min   (stage and commit local)
STEP 3  →  1 min   (log to CHANGES.md)
STEP 4  →  5 min   (build paper-watch + schedule task)
STEP 5  →  2 min   (update ACTIVE_TASKS.md)
STEP 6  →  2 min   (update SESSION_HANDOFF.md)
STEP 7  →  3 min   (sync + commit vault + report)
─────────────────────
TOTAL:     ~17 min  (deliberate pace, no rushing)
```

---

*Post-Backtest Commit & Next Steps Workflow v1.0 | Ronsi95 AI OS | May 2026*
*Built by Claude Opus 4 (planning) for Claude Code (execution)*
