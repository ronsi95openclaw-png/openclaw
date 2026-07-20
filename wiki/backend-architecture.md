# Backend Architecture — OpenClaw
> [[index]] | [[system-audit]] | Real execution flows only

## System Entry Points

```
Ronnie
 ├── Telegram → content/receiver.py (32 handlers, python-telegram-bot)
 └── Browser  → dashboard/app.py (Flask, port 8080)
```

## Core Brain Flow

```
Message arrives
  → ask_hybrid(message, system, history)
      ├── Cache check → data/response_cache.json (TTL 1h)
      ├── Complexity check (keyword list)
      │     ├── Simple → Ollama (gemma3:4b, local, free)
      │     └── Complex → Claude Haiku API (claude-haiku-4-5)
      └── Response logged to data/usage_stats.json
```

## Telegram Bot Flow

```
Update → python-telegram-bot dispatcher
  → auth check (ALLOWED_CHAT_ID)
  → CommandHandler routes to async cmd_*
  → cmd_* calls module → returns text
  → update.message.reply_text(result)
```

**Auth model:** All commands check `update.effective_chat.id == int(ALLOWED_CHAT_ID)`.  
`/run` and `/py` also check blocklist patterns before execution.

## Dashboard Data Flow

```
Browser GET /
  → index() collects:
      get_usage_today()      → data/usage_stats.json
      get_prices()           → CoinGecko API (20s cache)
      get_ollama_status()    → ollama.list()
      get_clawbot_status()   → data/usage_stats.json mtime
      get_tasks()            → data/tasks.json
      get_recent_trades()    → data/logs/trades.log (JSONL)
      get_autotrade_status() → data/autotrade.json
      get_backtest_summary() → data/backtest_results.json
      get_notes_summary()    → data/knowledge/notes.json
      get_last_code_review() → data/code_reviews/*.md
      get_installed_skills() → skills/ directory listing
  → render_template_string(DASHBOARD_HTML, **all_data)
```

## Agent Chat Flow (NEW)

```
Browser POST /api/chat/agent {"agent": "HAWK", "message": "btc?"}
  → api_chat_agent()
      → context injection per agent:
          SCOUT   → data/job_scout_state.json
          WATCHDOG → get_prices() + get_recent_trades()
          HAWK    → get_prices()
          CODEX   → ROOT.rglob("*.py") count + last review
          CLIPPER → data/applier_state.json + outreach detection
          JARVIS  → no extra context (pure brain)
      → ask_hybrid(context+message, system=AGENT_SYSTEMS[agent])
      → returns {"reply", "brain", "agent"}
```

**CLIPPER special:** if message looks like outreach text (>80 chars, starts with greeting), auto-routes to `agents.human_voice.humanize()` → Claude Haiku rewrite, skips regular LLM.

## CashClaw Pipeline Flow (agent-side, Telegram NOT wired)

```
agents/job_scout.py
  run_job_scout()
    → search Whop/Discord/Upwork (27 terms, 5 categories)
    → score each job via ask_hybrid()
    → save to data/job_scout_state.json
    → notify Telegram (if bot/chat_id provided)

  [Ronnie: /approve_job N] ← NOT WIRED
    → updates pending_jobs[N].approved = True

  [Ronnie: /apply_job N] ← NOT WIRED
    → agents/cashclaw_applier.py generate_apply()
        → quality_gate() (blocks stubs, bad platforms)
        → agents/human_voice.py generate_outreach()
            → Ollama draft → Claude Haiku rewrite
        → saves to data/applier_state.json

  [Ronnie: /send_apply N] ← NOT WIRED
    → confirm_apply() marks sent, moves to applied list
```

## Scheduler Jobs (APScheduler)

| Job ID | Interval | Function | Status |
|---|---|---|---|
| autotrade | Configurable | `cmd_scan` logic | ✅ Wired |
| reminders | Per-reminder | `send_reminder()` | ✅ Wired |
| cashclaw_scout | Every 6h | `run_job_scout()` | ❌ NOT registered |
| stale_task_sweep | Every 12h | `sweep_stale_tasks()` | ❌ NOT registered |

## Data Files

| File | Written by | Read by |
|---|---|---|
| data/usage_stats.json | core/brain.py | dashboard, /brain |
| data/tasks.json | core/scheduler.py | dashboard, /tasks |
| data/logs/trades.log | trading/executor.py | dashboard, /trades |
| data/autotrade.json | /autotrade command | dashboard, scheduler |
| data/backtest_results.json | trading/backtest.py | dashboard |
| data/knowledge/notes.json | /save command | dashboard, /notes |
| data/job_scout_state.json | agents/job_scout.py | /api/chat/agent (SCOUT) |
| data/applier_state.json | agents/cashclaw_applier.py | /api/chat/agent (CLIPPER) |
| data/income_log.json | /log_income (planned) | /clip-economy |
| data/taskboard.json | /api/taskboard/* | /taskboard |
| data/custom_agents.json | /api/agent/create | /team |
| data/team.json | Manual / static | /team |

## Security Model

- **Auth:** `ALLOWED_CHAT_ID` whitelist — all Telegram handlers check this
- **Shell:** `/run` uses `shell=True` (intentional, auth-gated) + `_BLOCKED_PATTERNS` blocklist
- **Dashboard:** `DASHBOARD_TOKEN` env var (optional); if not set → localhost-only trust mode
- **API keys:** All in `.env`, never hardcoded — `load_dotenv(override=True)` at startup
- **Headers:** X-Content-Type-Options, X-Frame-Options, CSP set on all responses
