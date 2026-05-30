# CRYPTOBOT — NEXT SESSION WORKFLOW
## Claude Code Workflow Command | Ronsi95 AI OS | May 2026
## Save as: C:\Users\ronsi95openclaw\Claude-openclaw\workflows\next_session.md

> **HOW TO USE:**
> 1. Save this file to `Claude-openclaw\workflows\next_session.md`
> 2. Open Claude Code in the `Claude-openclaw` workspace
> 3. Run: `/workflow next_session`
> 4. Claude Code executes phases in order, stops at checkpoints for your input

---

## 🧠 IDENTITY & MISSION

You are Claude Code resuming Ronnie's CryptoBot work. Last session ended in a
disciplined state:
- 260 tests passing (CryptoBot 164 + HaulYeah 96)
- ClawBot v0.9 in DEMO mode (PID active)
- HaulYeah in DRY_RUN mode
- Auth blocker: Crypto.com returns 401 (key needs regenerating)
- Strategy decision pending: 4 backtested on 50 days, no winner yet across regimes

This workflow's mission: **unblock auth, pull a real backtest, decide a strategy,
do not go live without evidence.**

---

## ⚠️ ABSOLUTE RULES

1. Load Ruflo first (Step 0 — non-negotiable)
2. Never read `.env` values — only check presence
3. Never flip `TRADING_MODE` to LIVE in this session
4. Never wire a strategy into the executor based on 50-day data
5. Never push to GitHub without explicit "yes push"
6. Every change goes in `memory/CHANGES.md` with category + git tag
7. If 401 doesn't clear after key refresh → STOP and escalate

---

## ⚠️ ADAPTATION NOTES (filled in from prior session)

- Ruflo path in this repo: NOT at `04_SKILLS\ruflo\SKILL.md` (that's the RONSI95-OS
  template path that doesn't exist here). Skip Phase 0's Ruflo load OR look in
  the vault under `ai_core/skills/ruflo_skill.md` instead.
- Crypto.com endpoint used by this repo is `https://api.crypto.com/exchange/v1/public/get-candlestick`
  (NOT the v2 endpoint shown in the original draft). The pre-fetch script the
  prior session created uses the correct endpoint with `start_ts`/`end_ts`
  pagination — see `infra/fetch_historical_candles.py`.
- HaulYeah memory lives at `trash_hauling_bot/memory/` (consolidated repo,
  not the RONSI95-OS workspace).
- vault sync script lives at `infra/sync_to_vault.bat` (not `sync_to_vault.bat`
  at workspace root).

---

## 📋 WORKFLOW PHASES

```
PHASE 0  → Load Ruflo + read continuity files
PHASE 1  → Fix Crypto.com 401 (auth verification)   [GATED — user must refresh key]
PHASE 2  → Update STARTING_BALANCE_USD to reality   [needs Phase 1]
PHASE 3  → Pull 1+ year of historical candles       [PRE-DONE — see data/backtest/]
PHASE 4  → Run full backtest comparison
PHASE 5  → Analyze regime resilience (not single-period winners)
PHASE 6  → Document strategy decision (NO executor wiring yet)
PHASE 7  → Update daily routine with auth check
PHASE 8  → Sync vault + commit + Telegram summary
```

Total estimated time: 40 minutes (Phase 3 data already fetched ahead).

---

## STEP 0 — LOAD CONTINUITY

```powershell
$root = "C:\Users\ronsi95openclaw\Claude-openclaw"

Write-Host "`n=== LAST SESSION HANDOFF ==="
Get-Content "$root\trash_hauling_bot\memory\SESSION_HANDOFF.md"

Write-Host "`n=== ACTIVE TASKS ==="
Get-Content "$root\trash_hauling_bot\memory\ACTIVE_TASKS.md"
```

State out loud:
- Last session: 2026-05-30
- Open blockers: Crypto.com 401, STARTING_BALANCE_USD placeholder, strategy decision
- Today's mission: unblock auth → real backtest → strategy decision (no live wire)

---

## PHASE 1 — FIX CRYPTO.COM 401

### Step 1A: Verify current state
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw

# Confirm verifier exists
Test-Path "infra\verify_cryptocom_auth.py"

# Confirm .env has both keys (presence only, no values)
python -c "from dotenv import dotenv_values; v = dotenv_values('.env'); print('CRYPTOCOM_API_KEY present:', 'CRYPTOCOM_API_KEY' in v and len(v.get('CRYPTOCOM_API_KEY','')) > 0); print('CRYPTOCOM_SECRET present:', 'CRYPTOCOM_SECRET' in v and len(v.get('CRYPTOCOM_SECRET','')) > 0)"
```

### Step 1B: Ask Ronnie to refresh the key

Print and wait:

```
═══════════════════════════════════════════════════════════════════
CRYPTO.COM API KEY REFRESH — MANUAL STEP

1. crypto.com/exchange → Settings → API Keys
2. Delete the existing key
3. Create New Key with: ✅ READ  ✅ TRADE  ❌ WITHDRAW
4. (Optional) IP allowlist your home IP
5. Open .env, update CRYPTOCOM_API_KEY= and CRYPTOCOM_SECRET=
6. Save .env

Reply "key refreshed" when done.
═══════════════════════════════════════════════════════════════════
```

### Step 1C: Run the verifier
```powershell
cd C:\Users\ronsi95openclaw\Claude-openclaw
python -m infra.verify_cryptocom_auth
```

- ✅ Success → capture balance value for Phase 2
- ❌ Still 401 → retry once after 60s, then HALT and escalate

---

## PHASE 2 — UPDATE STARTING_BALANCE_USD

After auth verifies, the verifier prints the real portfolio USD. Update
.env's `STARTING_BALANCE_USD` to that value so the circuit breaker
measures drawdown from reality (not the legacy $96 placeholder).

```powershell
# After Ronnie updates .env:
python -c "from dotenv import dotenv_values; print('STARTING_BALANCE_USD:', dotenv_values('.env').get('STARTING_BALANCE_USD'))"
```

---

## PHASE 3 — HISTORICAL DATA (already fetched)

**Hard-learned constraint:** Crypto.com Exchange v1 public endpoint caps at
**300 candles per call** regardless of `count` (tested 5000/1000/300 — all
returned exactly 300). `end_ts` pagination not supported on the public
endpoint. The only lever for more calendar coverage is the **timeframe**.

Two datasets are pre-fetched and live on disk:

| File                              | Candles | Span    | Use for                  |
|-----------------------------------|---------|---------|--------------------------|
| `data/backtest/{SYM}_4h_1y.json`  | 300     | ~49 d   | high-frequency signals   |
| `data/backtest/{SYM}_1d_1y.json`  | 300     | ~299 d  | regime resilience (4 Q)  |

```powershell
Get-ChildItem "data\backtest" -Filter "*.json" | Select-Object Name, LastWriteTime, @{N='SizeKB';E={[math]::Round($_.Length/1024,1)}}
```

If files are missing or stale (>7 days):
```powershell
python infra\fetch_historical_candles.py 4h
python infra\fetch_historical_candles.py 1d
```

For PHASE 4-5 regime testing, **use the `1d` files** (10 months of daily
data = enough to split into 4 quarters with real variation). The `4h` files
are useful for next-next-session paper-trade comparison once the strategy is
wired into the live executor at 4h cadence.

**Quarterly slices on daily data:**
  Q1 ≈ days 1-75    Q2 ≈ 76-150    Q3 ≈ 151-225    Q4 ≈ 226-299
  → each quarter is ~75 daily candles, enough for a strategy to fire
    several signals if it's going to fire at all.

---

## PHASE 4 — RUN FULL BACKTEST COMPARISON

Use the existing `trading/backtest.py` harness plus the 4 plug-in strategies
already in `trading/strategies/`. The comparison runner from this workflow
template loops every strategy × every symbol × the full 1-year window AND
splits BTC into 4 quarters for regime resilience.

```powershell
python infra\run_strategy_comparison.py
```

(Build that file from the template's Phase 4A if it doesn't exist yet.)

---

## PHASE 5 — REGIME RESILIENCE

Look at the `+Quarters` column. Pick the strategy with the highest count of
positive quarters (4/4 ideal, 3/4 acceptable). Magnitude of single-period
PnL is secondary — regime resilience beats one lucky run.

If NO strategy hits 3/4+ → DO NOT wire anything. Pull more data (2 yr) or
try an ensemble.

---

## PHASE 6 — DOCUMENT DECISION (NO WIRING)

Write the decision note in the vault. Wiring is deferred to the NEXT-NEXT
session after 14 days of paper trading the candidate.

---

## PHASE 7 — DAILY ROUTINE UPDATE

Add `python -m infra.verify_cryptocom_auth` to your morning routine so a
revoked/expired key surfaces immediately.

---

## PHASE 8 — COMMIT, SYNC, REPORT

```powershell
git add infra/ data/backtest/comparison_*.json trash_hauling_bot/memory/
git commit -m "feat(backtest): 1yr historical + 5-strategy comparison; strategy decision deferred"
# Do NOT push without "yes push" — confirm with Ronnie first

& "C:\Users\ronsi95openclaw\Claude-openclaw\infra\sync_to_vault.bat"

# Vault commit (separate repo)
$vault = "$env:USERPROFILE\Documents\Obsidian Vault"
git -C $vault add "30 - Crypto/"  # or wherever the backtest note lives
git -C $vault commit -m "vault: 1yr backtest decision deferred to next paper-trade session"
```

---

## 📌 WHAT THIS WORKFLOW NEVER DOES

- Wire a new strategy into the live executor in the same session as backtesting
- Flip TRADING_MODE to LIVE
- Add capital before paper testing the new strategy
- Push to GitHub automatically
- Read .env values
- Modify risk parameters
- Skip the auth check if 401 persists
- Recommend a strategy based on a single market regime

---

*Next Session Workflow v1.0 | Ronsi95 AI OS | May 2026*
*Adapted from RONSI95-OS template to the real Claude-openclaw layout (2026-05-30).*
