# Improvement Roadmap — OpenClaw
> [[index]]
> Priority ranked by impact | [[system-audit]] | [[failure-log]] | [[autopilot-audit]]

---

## P0 — Critical (blocks income pipeline)

### 1. Wire CashClaw commands into receiver.py
**Impact:** Without this, the entire income engine is inaccessible from Telegram.  
**Effort:** Medium (write 6 handlers + register in main())  
**Fix:** See [[failure-log#FAIL-001]]  
**Commands needed:** `/cashclaw`, `/scout`, `/approve_job`, `/apply_job`, `/send_apply`, `/discard_apply`

### 2. Add APScheduler jobs for CashClaw + stale sweep
**Impact:** Scout currently never runs automatically. Stale tasks pile up.  
**Effort:** Low (2 scheduler.add_job calls in receiver.py main())  
```python
scheduler.add_job(run_job_scout, "interval", hours=6, id="cashclaw_scout")
scheduler.add_job(sweep_stale_tasks, "interval", hours=12, id="stale_sweep")
```

---

## P1 — High (multi-agent infrastructure)

### 3. Implement orchestrator utility functions
**Impact:** `forward_message`, `validate_agent_output`, `sweep_stale_tasks` referenced in design but missing.  
**Effort:** Low (add ~60 lines to agent_team_orchestrator.py)  
**Fix:** See [[failure-log#FAIL-003]]

### 4. Add /fng Telegram handler
**Impact:** Dashboard quick bar shows `/fng` but it doesn't work in Telegram.  
**Effort:** Very low (10 lines + 1 registration)  
**Fix:** See [[failure-log#FAIL-002]]

---

## P2 — Medium (data + income tracking)

### 5. /log_income Telegram command
**Impact:** `/clip-economy` dashboard shows "$0 — no income logged yet" forever without this.  
**Effort:** Low  
```python
async def cmd_log_income(update, context):
    # args: /log_income 150 whop "clip job"
    amount, source, *note = context.args
    entry = {"amount": float(amount), "source": source,
             "note": " ".join(note), "timestamp": datetime.utcnow().isoformat()}
    log = _read_json(DATA_DIR/"income_log.json", [])
    log.append(entry)
    (DATA_DIR/"income_log.json").write_text(json.dumps(log, indent=2))
    await update.message.reply_text(f"Logged ${amount} from {source}")
```

### 6. Fix Holdings (Crypto.com API keys)
**Impact:** Holdings page shows error on every load.  
**Effort:** Zero code change — regenerate keys on Crypto.com Exchange with Read permission, no IP restriction.

---

## P3 — Low (polish + reliability)

### 7. /discard_apply — implement discard_draft() in cashclaw_applier.py
`discard_draft()` function not yet defined. Needed for the discard flow.

### 8. Google Sheets integration
`/report` calls `agents.sheets_agent.run_report()` but Google credentials not configured. Add `GOOGLE_SHEETS_CREDS` to `.env`.

### 9. Whisper voice handler end-to-end test
`voice/` directory exists, `WHISPER_MODEL=base` in `.env`, but voice message → transcription → ask_hybrid flow not verified live.

### 10. Backtest → auto-strategy selection
Currently `/backtest run` outputs rankings but doesn't auto-select the top strategy for `/autotrade`. Could auto-update `autotrade.json` with best-performing params.

---

## Progress Tracker

| # | Item | Status |
|---|---|---|
| 1 | Wire CashClaw commands | ✅ DONE (2026-04-17) |
| 2 | APScheduler CashClaw jobs | ✅ DONE (2026-04-17) |
| 3 | Orchestrator utility functions | ✅ DONE (2026-04-17) |
| 4 | /fng handler | ✅ DONE (2026-04-17) |
| 5 | /log_income handler | ✅ DONE (2026-04-17) |
| 6 | Fix Holdings API keys | ⬜ TODO |
| 7 | discard_draft() function | ✅ DONE (2026-04-17) |
| 8 | Google Sheets config | ⬜ OPTIONAL |
| 9 | Whisper end-to-end test | ⬜ OPTIONAL |
| 10 | Backtest → auto-strategy | ⬜ OPTIONAL |
