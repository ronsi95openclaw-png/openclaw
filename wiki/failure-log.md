# Failure Log — OpenClaw
> [[index]]
> [[system-audit]] | [[improvement-roadmap]] | [[autopilot-audit]]

---

## FAIL-001 — CashClaw Pipeline Commands Not Wired

**Severity:** CRITICAL  
**Category:** Missing registration  
**Affected:** `/cashclaw`, `/scout`, `/approve_job`, `/apply_job`, `/send_apply`, `/discard_apply`, `/sweep`

**Root cause:**  
Agent modules exist and are functional (`agents/job_scout.py`, `agents/cashclaw_applier.py`, `agents/human_voice.py`) but `CommandHandler` registrations were never added to `content/receiver.py`. The handlers and the APScheduler jobs for the scout cycle were planned in a prior session but not committed to the file.

**Evidence:**  
`grep "cashclaw\|apply_job" content/receiver.py` → 0 results  
`grep "def run_job_scout" agents/job_scout.py` → line 384 (exists)

**Fix required:**  
Add 8 `async def cmd_*` functions to `receiver.py` and register them in `main()`.

```python
# In content/receiver.py — add these handlers:

async def cmd_cashclaw(update, context):
    from agents.job_scout import get_scout_status
    from agents.cashclaw_applier import get_applier_status
    scout = get_scout_status()
    applier = get_applier_status()
    await update.message.reply_text(
        f"CashClaw Status\n"
        f"Pending jobs: {scout.get('pending',0)}\n"
        f"Approved: {scout.get('approved',0)}\n"
        f"Drafts ready: {applier.get('pending_drafts',0)}\n"
        f"Applied: {scout.get('applied',0)}"
    )

async def cmd_scout(update, context):
    args = context.args
    if args and args[0] == "run":
        from agents.job_scout import run_job_scout
        result = run_job_scout()
        await update.message.reply_text(result[:4000])
    else:
        await cmd_cashclaw(update, context)

async def cmd_apply_job(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /apply_job <index>")
        return
    from agents.cashclaw_applier import generate_apply
    result = generate_apply(int(context.args[0]) - 1)
    await update.message.reply_text(str(result)[:4000])

async def cmd_send_apply(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /send_apply <draft_index>")
        return
    from agents.cashclaw_applier import confirm_apply
    result = confirm_apply(int(context.args[0]) - 1)
    await update.message.reply_text(str(result)[:4000])

async def cmd_discard_apply(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /discard_apply <draft_index>")
        return
    from agents.cashclaw_applier import discard_draft
    result = discard_draft(int(context.args[0]) - 1)
    await update.message.reply_text(str(result)[:4000])

# In main() — register all:
_app.add_handler(CommandHandler("cashclaw",    cmd_cashclaw))
_app.add_handler(CommandHandler("scout",       cmd_scout))
_app.add_handler(CommandHandler("apply_job",   cmd_apply_job))
_app.add_handler(CommandHandler("send_apply",  cmd_send_apply))
_app.add_handler(CommandHandler("discard_apply", cmd_discard_apply))
```

---

## FAIL-002 — /fng No Telegram Handler

**Severity:** LOW  
**Category:** Missing handler  
**Affected:** Dashboard quick-command bar, Telegram

**Root cause:**  
Dashboard shows `/fng` as a quick-command button (copies to clipboard). No `cmd_fng` function or `CommandHandler("fng", ...)` exists in `receiver.py`.

**Fix:**
```python
async def cmd_fng(update, context):
    import requests
    r = requests.get("https://api.alternative.me/fng/", timeout=8)
    d = r.json()["data"][0]
    await update.message.reply_text(
        f"Fear & Greed Index: {d['value']} ({d['value_classification']})"
    )

# In main():
_app.add_handler(CommandHandler("fng", cmd_fng))
```

---

## FAIL-003 — Orchestrator Multi-Agent Functions Missing

**Severity:** MEDIUM  
**Category:** Missing implementation  
**Affected:** `skills/agent_team_orchestrator.py`

**Root cause:**  
`forward_message()`, `validate_agent_output()`, and `sweep_stale_tasks()` were designed per the multi-agent patterns spec in a prior session, but are not present in the current `agent_team_orchestrator.py` file. The `/sweep` Telegram command also depends on `sweep_stale_tasks`.

**Fix — add to `skills/agent_team_orchestrator.py`:**
```python
def forward_message(task_id: str, agent_id: str, message: str,
                    to_user: bool = True, metadata: dict | None = None) -> dict:
    """Pass message directly without supervisor paraphrase."""
    task = _orchestrator.tasks.get(task_id)
    if task:
        task.comments.append({
            "author": agent_id, "text": f"[forwarded] {message[:500]}",
            "ts": datetime.now(timezone.utc).isoformat()
        })
        _orchestrator.save_tasks()
    return {"type": "direct_response" if to_user else "supervisor_input",
            "content": message, "from": agent_id}

def validate_agent_output(output: dict, required_keys: list,
                          agent_id: str = "", task_id: str = "",
                          log_failure: bool = True) -> tuple[bool, list]:
    """Validate output schema between agents."""
    missing = [k for k in required_keys if k not in output]
    if missing and log_failure:
        try:
            from agents.failure_memory import log_lesson
            log_lesson(f"Agent {agent_id} output missing keys: {missing}",
                       tags=["validation", agent_id])
        except Exception:
            pass
    return (len(missing) == 0), missing

def sweep_stale_tasks(ttl_hours: int = 48) -> list:
    """Mark tasks not updated within TTL as stale."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    expired = []
    for task_id, task in _orchestrator.tasks.items():
        if task.state in ("done", "stale"):
            continue
        updated = datetime.fromisoformat(task.updated_at) if task.updated_at else None
        if updated and (now - updated).total_seconds() > ttl_hours * 3600:
            task.state = "stale"
            expired.append(task_id)
    if expired:
        _orchestrator.save_tasks()
    return expired
```

---

## FAIL-004 — Holdings Broken (10002 UNAUTHORIZED)

**Severity:** MEDIUM  
**Category:** API authentication  
**Affected:** GET /holdings, `trading/exchange.py`

**Root cause:**  
`CRYPTOCOM_API_KEY` and `CRYPTOCOM_SECRET` in `.env` return error 10002 from Crypto.com API. Keys may be: expired, IP-restricted, or missing `read` permission.

**Fix options (in order):**  
1. Re-generate keys on Crypto.com Exchange → Settings → API Management → ensure "Read" permission, no IP restriction
2. Or use sandbox mode for testing
3. Dashboard already shows user-friendly error message — no code fix needed, just key fix

---

## FAIL-005 — Ollama Model Mismatch (Resolved)

**Severity:** LOW (resolved 2026-04-17)  
**Category:** Configuration  

`.env` had `OLLAMA_MODEL=gemma4` (not installed). Fixed to `gemma3:4b`.  
`core/brain.py` DEFAULT_OLLAMA_MODEL also updated.  
**Status:** ✅ RESOLVED
