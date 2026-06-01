# CRYPTOBOT — DAILY OPERATING ROUTINE
## Adapted from v2.1 template, real paths confirmed 2026-05-31

> Open Claude Code in `C:\Users\ronsi95openclaw\Claude-openclaw\` each morning
> and paste this file. It walks the routine in order with explicit checkpoints.

---

## STEP 0 — LOAD CONTRACTS

```powershell
$root = "C:\Users\ronsi95openclaw\Claude-openclaw"
$vaultClaude = "$env:USERPROFILE\Documents\Obsidian Vault\CLAUDE.md"

# Vault contract (lives in vault per 2026-05-31 reorg)
if (Test-Path $vaultClaude) { Get-Content $vaultClaude }

# Bot repo continuity (real paths — memory/ at root, NOT 02_CRYPTOBOT/memory/)
Get-Content "$root\memory\SESSION_HANDOFF.md"
Get-Content "$root\memory\ACTIVE_TASKS.md"
Get-Content "$root\memory\CHANGES.md" -Tail 20
```

State out loud:
- Yesterday's commit SHA and what's pending push
- Any open ACTIVE_TASKS that crossed the night
- Whether vault is in sync

---

## STEP 1 — MORNING NOTIFICATION

If `memory/CHANGES.md` shows applied Category A entries since last session,
send Telegram first:

```python
import os, time
from dotenv import load_dotenv
load_dotenv()
chat_id = os.getenv("TELEGRAM_CHAT_ID")  # confirmed real env var (not TELEGRAM_ALLOWED_CHAT_IDS)
# Build morning summary from overnight CHANGES.md entries
```

---

## STEP 2 — BOT HEALTH CHECK

```powershell
$root = "C:\Users\ronsi95openclaw\Claude-openclaw"

Write-Host "=== ClawBot process (precise: -m content.receiver) ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*-m content.receiver*" } |
  Select-Object ProcessId, CreationDate

Write-Host "`n=== Ollama (graceful fallback to Claude API if down) ==="
try {
    $r = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3
    Write-Host "Ollama: $($r.StatusCode)"
} catch {
    Write-Host "Ollama: DOWN (Claude API fallback in effect)"
}

# Crypto.com auth — post-2026-05-31 migration this hits v2/private/get-account-summary
Write-Host "`n=== Crypto.com auth (v2) ==="
cd $root
python -m infra.verify_cryptocom_auth
if ($LASTEXITCODE -ne 0) {
    Write-Host "Crypto.com auth FAILED — escalate Category C, halt trading ops"
}

Write-Host "`n=== Scheduled tasks ==="
schtasks /query /tn "ClawBot-LiquiditySweep-Watch" /fo LIST |
  Select-String "Status|Last Result|Next Run"
schtasks /query /tn "ClawBot-Watchdog" /fo LIST |
  Select-String "Status|Last Result|Next Run"
```

---

## STEP 3 — PULL LAST 24H DATA

```python
from datetime import datetime, timedelta
# Bot's trade log:
import json
from pathlib import Path

log = Path("data/logs/trades.log")
if not log.exists():
    print("No trade log yet")
else:
    cutoff = (datetime.utcnow() - timedelta(hours=24))
    recent = []
    for line in log.read_text().splitlines():
        if line.startswith("TRADE_DECISION"):
            ts = line.split(" | ")[1]
            if datetime.fromisoformat(ts.replace("+00:00","")) >= cutoff:
                recent.append(json.loads(line.split(" | ")[2]))
    print(f"Trades 24h: {len(recent)}")
    if recent:
        pnl = sum(t.get("pnl", 0) for t in recent if t.get("pnl") is not None)
        wins = sum(1 for t in recent if (t.get("pnl") or 0) > 0)
        print(f"P&L: ${pnl:+.2f}  W/L: {wins}/{len(recent)-wins}")
```

---

## STEP 4 — DAILY COMPLIANCE CHECK (7 rules)

```
[ ] Rule 1: DEMO/PAPER mode active
            -> trading/mode.py get_mode() returns "DEMO"
            -> FAIL = halt trading until manual override

[ ] Rule 2: Risk-per-trade hardcode sane
            -> trading/executor.py:126 calculate_position_size(..., risk_pct=1.5)
            -> trading/strategy.py:203 default risk_pct=1.5 (1.5% per trade)
            -> No env var (MAX_TRADE_RISK_PCT does not exist in this codebase)
            -> FAIL only if the hardcoded value has been silently bumped; if so,
               Category C escalation (do not adjust risk parameters)
            -> Future: extract to .env when proper config loader exists

[ ] Rule 3: Circuit breaker armed against real baseline
            -> .env STARTING_BALANCE_USD (set 2026-05-31 to 96.39)
            -> .env MAX_DRAWDOWN_PCT (0.20 = -20% trigger)
            -> Distance from breaker: current_balance / starting_balance ratio

[ ] Rule 4: News filter status (N/A until module exists)
            -> CLAUDE.md mentions `agents/news_filter` aspirationally but the
               agents/ directory is empty in the current codebase
            -> Pass by default; revisit when the module is actually built
            -> Then: verify it imports and today's news is classified

[ ] Rule 5: Max active coins respected
            -> count of open positions <= configured ceiling

[ ] Rule 6: No premature capital added
            -> balance tracked vs starting baseline

[ ] Rule 7: Sunday weekly review done (if today is Monday)
```

Any FAIL -> fix in Step 6 (if Category A) or escalate (B/C).

---

## STEP 5 — DETECT STUCK STATE & ANOMALIES

- Stale open positions (>48h)
- Scheduled task last result != 0
- `data/paper_watch/liquidity_sweep.jsonl` not appending daily
- Watchdog or Liquidity-Sweep task last run more than expected interval ago
- Signal-to-trade ratio sanity (too permissive or too restrictive)

Anomalies -> log to memory/BUGS.md.

---

## STEP 6 — AUTO-FIX LOOP (Category A / B / C)

### CATEGORY A — Safe auto-fixes (apply with git tag for rollback)

| Issue | Fix | Daily cap |
|-------|-----|-----------|
| Bot process down | re-launch via start.py | 3 |
| Ollama not reachable | `ollama serve` (background) | 3 |
| Scheduled task last result != 0 | `schtasks /run /tn <name>` | 2 |
| Stale position > 48h | close at market, log reason | unlimited |
| Missing log dir | `mkdir data\logs` | 1 |
| Watchdog stopped | `schtasks /run /tn ClawBot-Watchdog` | 3 |
| Liquidity-sweep paper-watch task stopped | `schtasks /run /tn ClawBot-LiquiditySweep-Watch` | 3 |

Cap exceeded -> auto-escalates to Category C.

Before each fix:
```powershell
$tag = "autofix-$(Get-Date -Format 'yyyyMMdd-HHmm')-<id>"
git tag $tag
# apply fix; verify; log to memory/CHANGES.md with $tag
```

### CATEGORY B — Strategy/parameter proposals (NEVER auto-apply)

Write to `memory/STRATEGY_DECISIONS.md`, wait for explicit "yes apply".

### CATEGORY C — Always escalate (Telegram + memory log)

- Any change to `trading/risk.py`, `MAX_TRADE_RISK_PCT`, `MAX_DRAWDOWN_PCT`
- TRADING_MODE flip to LIVE
- Adding capital
- Disabling any filter
- API credentials
- Vault contract violations
- Same Category A fix hitting its daily cap
- The unverified `private/create-order` on v2 (ACTIVE_TASKS #1) is a hard
  prerequisite to ANY LIVE-mode consideration

---

## STEP 7 — UPDATE memory/CHANGES.md

Format:
```markdown
## [YYYY-MM-DD HH:MM] - [A/B/C] - <Title>
**Trigger:** what was detected (with the measurement)
**Action:** what was done (or proposed)
**Result:** what changed
**Files touched:** <list>
**Git tag:** autofix-...
**Approved by:** Auto / Ronnie (date)
**Status:** APPLIED / PENDING / REJECTED / ROLLED-BACK
---
```

---

## STEP 8 — DAILY TRADE JOURNAL (vault)

Append to (vault path, post-reorg structure):
```
C:\Users\ronsi95openclaw\Documents\Obsidian Vault\20 - OpenClaw\Memory\OPENCLAW_journal-YYYY-MM-DD.md
```

Frontmatter (vault contract requires):
```yaml
---
title: OpenClaw journal YYYY-MM-DD
created: YYYY-MM-DD
type: log
tags:
  - openclaw
  - openclaw/journal
status: active
---
```

Content sections: balance, trades count, compliance, notable trades,
bot health, auto-fixes, proposals pending, escalations, tomorrow's focus.

---

## STEP 9 — UPDATE MEMORY FILES (bot repo, root memory/)

Refresh in `memory/`:
- SESSION_HANDOFF.md (current snapshot)
- ACTIVE_TASKS.md (if priorities shifted)
- BUGS.md (new entries if anomalies found)
- CHANGES.md (already updated in Step 7)

---

## STEP 10 — SYNC VAULT

```powershell
& "C:\Users\ronsi95openclaw\Claude-openclaw\infra\sync_to_vault.bat"
```

Verify the OPENCLAW_*.md files land at:
```
C:\Users\ronsi95openclaw\Documents\Obsidian Vault\20 - OpenClaw\Memory\
```

Note: per vault contract from 2026-05-31 reorg, bare names like
`[[ACTIVE_TASKS]]` no longer resolve. Use prefixed names like
`[[OPENCLAW_ACTIVE_TASKS]]` in wiki-links. The sync_to_vault.bat handles
the renames automatically when patched (see ACTIVE_TASKS for the deferred
sync_to_vault.bat OPENCLAW_ patch).

---

## STEP 11 — GIT COMMIT (LOCAL ONLY)

```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
git add memory/ data/paper_watch/   # explicit paths, never `git add -A`
git -c user.email="clawbot@openclaw.local" -c user.name="ClawBot" `
  commit -m "daily: <date> - <N> trades, balance $<X>, compliance <X>/7, <N> auto-fixes"
```

Do NOT push unless Ronnie says "yes push" explicitly.

---

## STEP 12 — EOD TELEGRAM SUMMARY

```python
import os
from dotenv import load_dotenv
load_dotenv()
chat_id = os.getenv("TELEGRAM_CHAT_ID")
# Build EOD: balance, trade count, compliance, auto-fixes, proposals pending,
#           escalations, top-of-stack for tomorrow
```

---

## WEEKLY (Sundays — in addition to daily)

- Re-run backtest on this week's data via `infra/run_strategy_comparison.py`
- Update pre-live checklist progress in memory/PRE_LIVE_CHECKLIST.md (if exists)
- Weekly review note in vault
- After 2026-06-07: Day-7 LiquiditySweep paper-watch peek (read
  `data/paper_watch/liquidity_sweep.jsonl`, classify regime, count signals)
- On 2026-06-14: Day-14 paper-watch decision (wire / extend / retire); writes
  to memory/DECISIONS.md

---

## EMERGENCY PROTOCOLS

### Circuit breaker triggered
1. DO NOT restart the bot
2. Read Telegram alert + last 10 trades from data/logs/trades.log
3. Document in memory/BUGS.md
4. Wait for manual review
5. Resume only in DEMO after parameter adjustment

### Ronnie says "go live" prematurely
1. DO NOT flip TRADING_MODE
2. Print pre-live checklist status
3. Require: explicit override + 24h cooling-off + memory/DECISIONS.md entry
4. ACTIVE_TASKS #1 (verify `private/create-order` on v2 with small notional)
   MUST be completed first

### Crypto.com auth returns non-200 (401, 400, 500, etc.)
1. Run `python -m infra.verify_cryptocom_auth` once
2. Wait 60s, retry once (key propagation delay possible)
3. If still failing: Category C escalation, halt trading ops
4. Resume only after manual fix

### Auto-fix loop hits daily cap
1. STOP that specific fix
2. Treat as Category C
3. The recurring symptom is bigger than auto-fix can handle

---

## NEVER DO

- Modify `trading/risk.py` without approval
- Flip TRADING_MODE without checklist + 24h cooling-off + ACTIVE_TASKS #1 done
- Skip Step 0 (contract load)
- Apply Category B/C without explicit approval
- Push to GitHub without "yes push"
- Touch the vault outside `infra/sync_to_vault.bat`
- Modify global git config (always inline `-c user.email/name`)
- Use `git add -A` or `git add .` in either repo
- Edit `ai_core/skills/*` or `.obsidian/graph.json` in the vault (Bucket C —
  other Claude sessions' territory)
- Read .env VALUES; presence/length checks only

---

## END OF SESSION CHECKLIST

- [ ] Step 0: Contracts loaded
- [ ] Step 1: Morning notification (if overnight changes applied)
- [ ] Step 2: Health check + Crypto.com auth verified (v2 endpoint)
- [ ] Step 4: Compliance 7/7 (or failures escalated)
- [ ] Step 6: Auto-fixes applied with tags; proposals logged; escalations sent
- [ ] Step 7: CHANGES.md updated
- [ ] Step 8: Trade journal in vault with frontmatter contract
- [ ] Step 9: memory files updated
- [ ] Step 10: sync_to_vault.bat ran successfully
- [ ] Step 11: Local commit
- [ ] Step 12: EOD Telegram

Final print:
```
Daily routine complete.
Compliance: <X>/7
Balance: $<X>
Auto-fixes: <N>
Pending proposals: <N>
Escalations: <N>
Next session: <time>
```

---
*Adapted from RONSI95 v2.1 template to real Claude-openclaw paths*
*Real paths confirmed: 2026-05-31 — see DECISIONS.md for the v2 API migration*
