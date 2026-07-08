# Feature Map — OpenClaw
> [[index]]
> Last updated: 2026-04-17 | [[system-audit]] | [[autopilot-audit]]

## Telegram Commands (Registered & Functional)

| Command | Handler | Backend | Status |
|---|---|---|---|
| /start | cmd_start | Static welcome text | ✅ FUNCTIONAL |
| /help | cmd_help | Static command list | ✅ FUNCTIONAL |
| /ask | cmd_ask | `ask_hybrid()` → Ollama/Haiku | ✅ FUNCTIONAL |
| /plan | cmd_plan | `ask_hybrid()` + plan prompt | ✅ FUNCTIONAL |
| /research | cmd_research | `ask_hybrid()` + research prompt | ✅ FUNCTIONAL |
| /clear | cmd_clear | Clears `conversation_history.json` | ✅ FUNCTIONAL |
| /market | cmd_market | `core.market.get_market_summary()` → CoinGecko | ✅ FUNCTIONAL |
| /scan | cmd_scan | `trading.exchange.fetch_all_closes()` + RSI/MACD | ✅ FUNCTIONAL |
| /dca | cmd_dca | CoinGecko price → LLM DCA analysis | ✅ FUNCTIONAL |
| /run | cmd_run | Shell exec (auth-gated, blocklist-protected) | ✅ FUNCTIONAL |
| /py | cmd_py | Python exec (auth-gated, blocklist-protected) | ✅ FUNCTIONAL |
| /remind | cmd_remind | `core.scheduler.add_reminder()` → APScheduler | ✅ FUNCTIONAL |
| /tasks | cmd_tasks | `core.scheduler.get_reminders()` | ✅ FUNCTIONAL |
| /cancel | cmd_cancel | `core.scheduler.cancel_reminder()` | ✅ FUNCTIONAL |
| /status | cmd_status | System health check + env vars | ✅ FUNCTIONAL |
| /brain | cmd_brain | Reads `data/usage_stats.json` | ✅ FUNCTIONAL |
| /trades | cmd_trades | Reads `data/logs/trades.log` | ✅ FUNCTIONAL |
| /weather | cmd_weather | Open-Meteo API (no key needed) | ✅ FUNCTIONAL |
| /autotrade | cmd_autotrade | `data/autotrade.json` + scheduler | ✅ FUNCTIONAL |
| /save | cmd_save | `core.knowledge.save_note()` | ✅ FUNCTIONAL |
| /notes | cmd_notes | Reads `data/knowledge/notes.json` | ✅ FUNCTIONAL |
| /news | cmd_news | `agents.news_filter_agent.check_news_filter()` | ✅ FUNCTIONAL |
| /report | cmd_report | `agents.sheets_agent.run_report()` | ✅ FUNCTIONAL (requires Google Sheets config) |
| /backtest | cmd_backtest | `trading.backtest` → historical RSI/MACD | ✅ FUNCTIONAL |
| /codereview | cmd_codereview | `agents.code_review_agent` → LLM code audit | ✅ FUNCTIONAL |
| /orchestrate | cmd_orchestrate | `skills.agent_team_orchestrator.get_orchestrator()` | ✅ FUNCTIONAL |
| /otasks | cmd_otasks | Orchestrator task list | ✅ FUNCTIONAL |
| /selfimprove | cmd_selfimprove | `skills.self_improving` | ✅ FUNCTIONAL |
| /secondbrain | cmd_secondbrain | `skills.second_brain` | ✅ FUNCTIONAL |
| /upgrade | cmd_upgrade | `agents.auto_upgrade.run_auto_upgrade()` | ✅ FUNCTIONAL |
| /restart | cmd_restart | `os.execv()` process restart | ✅ FUNCTIONAL |
| /stop | cmd_stop | `os._exit(0)` | ✅ FUNCTIONAL |

## Telegram Commands (Newly Wired — 2026-04-17)

| Command | Expected Handler | Agent Module | Status |
|---|---|---|---|
| /cashclaw | cmd_cashclaw | `agents.job_scout` + `cashclaw_applier` | ✅ WIRED |
| /scout [run] | cmd_scout | `agents.job_scout.run_job_scout()` | ✅ WIRED |
| /approve_job N | cmd_approve_job | `agents.job_scout.approve_job()` | ✅ WIRED |
| /apply_job N | cmd_apply_job | `agents.cashclaw_applier.generate_apply()` | ✅ WIRED |
| /send_apply N | cmd_send_apply | `agents.cashclaw_applier.confirm_apply()` | ✅ WIRED |
| /discard_apply N | cmd_discard_apply | `agents.cashclaw_applier.discard_draft()` | ✅ WIRED |
| /log_income | cmd_log_income | writes `data/income_log.json` | ✅ WIRED |
| /sweep | cmd_sweep | `skills.agent_team_orchestrator.sweep_stale_tasks()` | ✅ WIRED |
| /fng | cmd_fng | Fear & Greed Index API | ✅ WIRED |

## Dashboard Pages

| Route | Template | Data Source | Status |
|---|---|---|---|
| GET / | DASHBOARD_HTML | Live: prices, usage, bot status, trades | ✅ FUNCTIONAL |
| GET /taskboard | TASKBOARD_HTML | `data/taskboard.json` | ✅ FUNCTIONAL |
| GET /team | TEAM_HTML | `data/team.json` | ✅ FUNCTIONAL |
| GET /portfolio | PORTFOLIO_HTML | `data/logs/trades.log` + CoinGecko | ✅ FUNCTIONAL |
| GET /holdings | HOLDINGS_HTML | Crypto.com API | ⚠️ BROKEN (10002 UNAUTHORIZED) |
| GET /clip-economy | CLIP_ECONOMY_HTML | `data/income_log.json` + agent states | ✅ FUNCTIONAL |

## Dashboard API Endpoints

| Endpoint | Method | Function | Status |
|---|---|---|---|
| /api/chat | POST | `ask_hybrid()` → Ollama/Haiku | ✅ FUNCTIONAL |
| /api/chat/agent | POST | Per-agent routing with context injection | ✅ FUNCTIONAL |
| /api/chat/clear | POST | Clears in-memory history | ✅ FUNCTIONAL |
| /api/chat/agent/clear | POST | Clears per-agent history | ✅ FUNCTIONAL |
| /api/taskboard | GET | Reads `data/taskboard.json` | ✅ FUNCTIONAL |
| /api/taskboard/add | POST | Writes to `data/taskboard.json` | ✅ FUNCTIONAL |
| /api/taskboard/update | POST | Updates task in JSON | ✅ FUNCTIONAL |
| /api/taskboard/delete | POST | Deletes task from JSON | ✅ FUNCTIONAL |
| /api/agent/create | POST | Writes to `data/custom_agents.json` | ✅ FUNCTIONAL |
| /api/clip-economy/stats | GET | Returns income projections | ✅ FUNCTIONAL |
| /api/task/update | POST | Orchestrator task state transitions | ✅ FUNCTIONAL |
| /api/team | GET | Reads `data/team.json` | ✅ FUNCTIONAL |

## Dashboard UI Buttons

| Button | Location | JS Action | Backend | Status |
|---|---|---|---|---|
| /scan, /market etc (quick bar) | Main dashboard | Copy to clipboard | None (clipboard only) | ✅ FUNCTIONAL |
| ► CHAT WITH JARVIS | JARVIS card | `openAgentChat('JARVIS')` | `/api/chat/agent` | ✅ FUNCTIONAL |
| ► CHAT WITH SCOUT | SCOUT card | `openAgentChat('SCOUT')` | `/api/chat/agent` + job state | ✅ FUNCTIONAL |
| ► CHAT WITH WATCHDOG | WATCHDOG card | `openAgentChat('WATCHDOG')` | `/api/chat/agent` + live prices | ✅ FUNCTIONAL |
| ► CHAT WITH CODEX | CODEX card | `openAgentChat('CODEX')` | `/api/chat/agent` + codebase stats | ✅ FUNCTIONAL |
| ► CHAT WITH CLIPPER | CLIPPER card | `openAgentChat('CLIPPER')` | `/api/chat/agent` + HumanVoice | ✅ FUNCTIONAL |
| ► CHAT WITH HAWK | HAWK card | `openAgentChat('HAWK')` | `/api/chat/agent` + live prices | ✅ FUNCTIONAL |
| CLR (chat) | Chat panel | `clearChat()` | `/api/chat/clear` or `/api/chat/agent/clear` | ✅ FUNCTIONAL |
| Add Task | Taskboard | `openAddModal()` | `/api/taskboard/add` | ✅ FUNCTIONAL |
| Move Task (→/←) | Taskboard | `moveTask()` | `/api/taskboard/update` | ✅ FUNCTIONAL |
| Delete Task (🗑) | Taskboard | `deleteTask()` | `/api/taskboard/delete` | ✅ FUNCTIONAL |
| + NEW AGENT | Team page | `openNewAgentModal()` | `/api/agent/create` | ✅ FUNCTIONAL |
| ClawBot Chat send | Chat panel | `sendChat()` | `/api/chat` | ✅ FUNCTIONAL |
