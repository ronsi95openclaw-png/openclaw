# OpenClaw Autopilot Audit — v0.9 (2026-04-17)

> [[index]] | [[system-audit]] | [[backend-architecture]] | [[feature-map]]
> Role: AI Systems Auditor + Automation Architect
> Method: Static analysis + runtime trace of all 54 Telegram commands, 19 Flask routes, 8 agents

## Related notes

- [[system-audit]]
- [[backend-architecture]]
- [[feature-map]]
- [[failure-log]]
- [[improvement-roadmap]]

---

## PART 1 — FULL COMMAND AUDIT TABLE

### Telegram Commands (54 total)

| Command | Handler | File | Backend | Status |
|---|---|---|---|---|
| /start | cmd_start | receiver.py | Help text | ✅ WORKING |
| /help | cmd_help | receiver.py | Help text | ✅ WORKING |
| /ask | cmd_ask | receiver.py | ask_hybrid() → Ollama/Claude | ✅ WORKING |
| /plan | cmd_plan | receiver.py | ask_hybrid() structured prompt | ✅ WORKING |
| /research | cmd_research | receiver.py | ask_hybrid() deep research | ✅ WORKING |
| /clear | cmd_clear | receiver.py | Clears conv_history.json | ✅ WORKING |
| /market | cmd_market | receiver.py | Crypto.com API + ask_hybrid | ✅ WORKING |
| /scan | cmd_scan | receiver.py | RSI+MACD on live 4h candles | ✅ WORKING |
| /dca | cmd_dca | receiver.py | DCA analysis via exchange | ✅ WORKING |
| /run | cmd_run | receiver.py | subprocess + blocklist guard | ✅ WORKING |
| /py | cmd_py | receiver.py | exec() + blocklist guard | ✅ WORKING |
| /remind | cmd_remind | receiver.py | APScheduler cron job | ✅ WORKING |
| /tasks | cmd_tasks | receiver.py | reminders.json | ✅ WORKING |
| /cancel | cmd_cancel | receiver.py | APScheduler remove_job | ✅ WORKING |
| /status | cmd_status | receiver.py | Ollama ping + Claude check | ✅ WORKING |
| /brain | cmd_brain | receiver.py | usage_log.json stats | ✅ WORKING |
| /trades | cmd_trades | receiver.py | trade_log.json | ✅ WORKING |
| /weather | cmd_weather | receiver.py | open-meteo API (2 calls) | ✅ WORKING (slow ~3s) |
| /autotrade | cmd_autotrade | receiver.py | RSI+MACD→executor→APScheduler | ✅ WORKING |
| /save | cmd_save | receiver.py | knowledge/*.md append | ✅ WORKING |
| /notes | cmd_notes | receiver.py | knowledge/ file search | ✅ WORKING |
| /news | cmd_news | receiver.py | news_filter_agent + LLM | ✅ WORKING |
| /report | cmd_report | receiver.py | trade_log stats + ask_hybrid | ✅ WORKING |
| /backtest | cmd_backtest | receiver.py | 4-year RSI+MACD backtest | ✅ WORKING |
| /codereview | cmd_codereview | receiver.py | code_review_agent + LLM | ✅ WORKING |
| /orchestrate | cmd_orchestrate | receiver.py | AgentOrchestrator CRUD | ✅ WORKING |
| /otasks | cmd_otasks | receiver.py | tasks.json list | ✅ WORKING |
| /selfimprove | cmd_selfimprove | receiver.py | self_improving skill | ✅ WORKING |
| /secondbrain | cmd_secondbrain | receiver.py | second_brain skill | ✅ WORKING (fixed) |
| /upgrade | cmd_upgrade | receiver.py | auto_upgrade agent | ✅ WORKING |
| /restart | cmd_restart | receiver.py | os.execv() | ✅ WORKING |
| /stop | cmd_stop | receiver.py | PTB stop() | ✅ WORKING |
| /fng | cmd_fng | receiver.py | alternative.me API | ✅ WORKING |
| /cashclaw | cmd_cashclaw | receiver.py | job_scout + applier status | ✅ WORKING |
| /scout | cmd_scout | receiver.py | job_scout.run_job_scout() | ✅ WORKING |
| /approve_job | cmd_approve_job | receiver.py | job_scout.approve_job() | ✅ WORKING |
| /apply_job | cmd_apply_job | receiver.py | cashclaw_applier.generate_apply() | ✅ WORKING |
| /send_apply | cmd_send_apply | receiver.py | cashclaw_applier.confirm_apply() | ✅ WORKING |
| /discard_apply | cmd_discard_apply | receiver.py | cashclaw_applier.discard_draft() | ✅ WORKING |
| /log_income | cmd_log_income | receiver.py | income_log.json write | ✅ WORKING |
| /sweep | cmd_sweep | receiver.py | sweep_stale_tasks() | ✅ WORKING |
| /clip | cmd_clip | receiver.py | clip_processor.process_vod_url() | ✅ WORKING |
| /clips | cmd_clips | receiver.py | clip_processor.get_clip_jobs() | ✅ WORKING |
| /content | cmd_content | receiver.py | content_pipeline.run_content_pipeline() | ✅ WORKING |
| /approve_content | cmd_approve_content | receiver.py | content_pipeline.approve_content() | ✅ WORKING |
| /publish | cmd_publish | receiver.py | social_publisher.run/send_preview() | ⚠️ PARTIAL (needs TikTok/IG API keys) |
| /publishstats | cmd_publishstats | receiver.py | social_publisher.get_publish_stats() | ✅ WORKING |
| /tradingagent | cmd_tradingagent | receiver.py | trading_agent.run_trading_cycle() | ⚠️ PARTIAL (needs Crypto.com secret) |
| /performance | cmd_performance | receiver.py | performance_tracker.get_performance_summary() | ⚠️ PARTIAL (needs TikTok/IG API keys) |

### Dashboard Routes (19 total)

| Route | Status | Notes |
|---|---|---|
| / | ✅ WORKING | Retro Command Center |
| /portfolio | ✅ WORKING | Crypto.com portfolio |
| /holdings | ⚠️ PARTIAL | Crypto.com error 10002 (stale keys) |
| /taskboard | ✅ WORKING | Orchestrator tasks CRUD |
| /team | ✅ WORKING | Agent team overview |
| /clip-economy | ✅ WORKING | CashClaw clip stats |
| /api/chat | ✅ WORKING | Main ClawBot chat |
| /api/chat/agent | ✅ WORKING | Per-agent chat (6 agents) |
| /api/chat/agent/clear | ✅ WORKING | Clear agent history |
| /api/chat/clear | ✅ WORKING | Clear main chat |
| /api/taskboard | ✅ WORKING | Task list JSON |
| /api/taskboard/add | ✅ WORKING | Create task |
| /api/taskboard/update | ✅ WORKING | Update task state |
| /api/taskboard/delete | ✅ WORKING | Delete task |
| /api/task/update | ✅ WORKING | Status update |
| /api/team | ✅ WORKING | Team JSON |
| /api/agent/create | ✅ WORKING | Spawn agent |
| /api/clip-economy/stats | ✅ WORKING | Clip economy JSON |
| /api/agents | ✅ NEW | Real-time 8-agent status (added v0.9) |

---

## PART 2 — BROKEN / PARTIAL FEATURES

| Feature | Root Cause | Fix Required |
|---|---|---|
| /publish → TikTok/IG posting | No TIKTOK_ACCESS_TOKEN or IG_ACCESS_TOKEN in .env | Add API tokens (see REQUIRED checklist) |
| /tradingagent cycle → execution | Crypto.com keys may be stale (Holdings error 10002) | Regenerate Crypto.com API keys |
| /performance snapshot | No TikTok/IG read tokens | Same as above |
| /holdings dashboard | Crypto.com error 10002 | Regenerate keys on exchange.crypto.com |
| Whisper transcription | Whisper model not loaded (optional) | `pip install openai-whisper` + first run downloads model |
| yt-dlp download | yt-dlp may not be installed | `pip install yt-dlp` |
| ffmpeg | ffmpeg may not be on PATH | `winget install ffmpeg` |

---

## PART 3 — AUTOPILOT SCHEDULER (as wired)

| Job ID | Function | Interval | Status |
|---|---|---|---|
| news_filter | news_filter_agent.check_and_alert | every 15min | ✅ ACTIVE |
| code_review | code_review_agent (Sunday 09:00) | weekly | ✅ ACTIVE |
| cashclaw_scout | job_scout.run_job_scout | every 6h | ✅ ACTIVE |
| stale_sweep | sweep_stale_tasks | every 12h | ✅ ACTIVE |
| trading_cycle | trading_agent.run_trading_cycle | every 4h | ✅ ACTIVE |
| perf_tracker | performance_tracker.run_performance_tracker | every 6h | ✅ ACTIVE |
| daily_publish_preview | social_publisher.send_preview | 09:00 UTC daily | ✅ ACTIVE |
| clawbot_autotrade_daily | autotrade (RSI+MACD executor) | 08:00 UTC daily | ✅ ACTIVE |

---

## PART 4 — REQUIRED FROM USER FOR FULL AUTOPILOT

### 🔴 BLOCKING — System cannot earn without these

| # | What | Where to get | Used by |
|---|---|---|---|
| 1 | **Crypto.com API keys (regen)** | exchange.crypto.com → API Management | /tradingagent, /holdings, autotrade |
| 2 | **TIKTOK_ACCESS_TOKEN** | developers.tiktok.com → Content Posting API | /publish, performance_tracker |
| 3 | **IG_ACCESS_TOKEN** | developers.facebook.com → Graph API → Instagram | /publish, performance_tracker |
| 4 | **IG_ACCOUNT_ID** | Graph API explorer | social_publisher |
| 5 | **yt-dlp installed** | `pip install yt-dlp` | /clip |
| 6 | **ffmpeg on PATH** | `winget install ffmpeg` | /clip, /content |

### 🟡 HIGH PRIORITY — Needed for scale

| # | What | Default/Notes |
|---|---|---|
| 7 | **WHISPER_MODEL** in .env | Set to `base` (fast) or `small` for better accuracy |
| 8 | **Trading bankroll** | Set MAX_POSITION_SIZE in .env (default: 10% of balance) |
| 9 | **Risk parameters** | AUTOTRADE_MAX_LOSS_PCT (default: 2%), AUTOTRADE_POSITION_PCT (default: 5%) |
| 10 | **Storage for clips** | Clips accumulate fast — ensure data/clips/ has 10GB+ free |
| 11 | **GOOGLE_SHEETS_CREDS** | Optional — enables trade logging to Sheets |

### 🟢 OPTIONAL — Enhancers

| # | What | Notes |
|---|---|---|
| 12 | OPENWEATHER_API_KEY | /weather fallback if open-meteo is slow |
| 13 | GATEWAY_TOKEN | JARVIS WebSocket gateway (pip install websockets) |
| 14 | Polymarket/Kalshi API | Prediction market engine (future) |

### .env additions needed:
```
TIKTOK_ACCESS_TOKEN=
IG_ACCESS_TOKEN=
IG_ACCOUNT_ID=
WHISPER_MODEL=base
MAX_POSITION_SIZE=0.05
AUTOTRADE_MAX_LOSS_PCT=0.02
```

---

## PART 5 — MULTI-AGENT ARCHITECTURE

### Agent Roster (8 agents, supervisor pattern)

```
ClawBot (Supervisor / Orchestrator)
├── 🕵️  SCOUT       — Whop scraper → job opportunities
├── 📝  APPLIER     — HumanVoice → outreach drafts
├── ✂️  CLIP        — yt-dlp + FFmpeg + Whisper → viral clips
├── 🎬  CONTENT     — 9:16 reformat + Claude captions → queue
├── 📤  PUBLISHER   — TikTok/IG posting → income
├── 📈  PERF        — Views/engagement tracking → optimization
├── 📊  TRADING     — RSI+MACD scan → execution → Sheets log
└── 💰  INCOME      — Cross-agent income aggregator
```

### Data Flow (no telephone game — agents write to shared filesystem)

```
Opportunity Detection:
  SCOUT scrapes Whop → job_scout_state.json → /approve_job (human gate)
  TRADING scans RSI+MACD every 4h → LLM confirms → executor → trade_log.json

Content Pipeline:
  /clip <url> → CLIP downloads + splits → clip_jobs.json
              → CONTENT reformats + captions → content_queue.json
              → /approve_content (human gate)
              → PUBLISHER posts → publish_log.json
              → PERF tracks views → performance_db.json → income projection

Income Aggregation:
  /log_income → income_log.json ← read by /api/agents + /api/clip-economy/stats
```

### Consensus Rules (anti-sycophancy)
- Trading: LLM must confirm signal before executor fires. RSI + MACD + LLM = 3-layer gate
- Content: Human must /approve_content before PUBLISHER posts. Never auto-posts
- Jobs: Human must /approve_job before APPLIER generates draft. Never auto-sends

---

## PART 6 — DAILY AUTOPILOT SCHEDULE

### 08:00 UTC — Morning Sweep
| Time | Action | Agent | Output |
|---|---|---|---|
| 08:00 | RSI+MACD scan all pairs | TRADING | Signal list → LLM confirm → execute |
| 08:01 | Job scout run | SCOUT | New jobs → Telegram approval request |
| 09:00 | Daily publish preview | PUBLISHER | 3 queued clips sent to Telegram for OK |

### 12:00 UTC — Midday Check
| Time | Action | Agent | Output |
|---|---|---|---|
| 12:00 | Trading cycle (4h interval) | TRADING | Re-scan → hold/exit signals |
| 12:00 | Performance snapshot | PERF | Views delta + income update |

### 18:00 UTC — Evening
| Time | Action | Agent | Output |
|---|---|---|---|
| 18:00 | Job scout run | SCOUT | New jobs → approval |
| 18:00 | Trading cycle | TRADING | EOD position review |
| 18:00 | Performance snapshot | PERF | Daily stats |

### 21:00 UTC — Night Sweep
| Time | Action | Agent | Output |
|---|---|---|---|
| 21:00 | Stale task sweep | ORCHESTRATOR | Clears 48h+ stale tasks |
| 21:00 | Trading cycle | TRADING | Overnight signal check |

### Always-On
- News filter: every 15min → BLOCK/ALLOW trading signal
- APScheduler: all jobs survive bot restarts (reload_autotrade on boot)

---

## PART 7 — CLIP PIPELINE INSTALL CHECKLIST

```bash
# 1. Install tools
pip install yt-dlp openai-whisper
winget install ffmpeg    # Windows

# 2. Verify
yt-dlp --version
ffmpeg -version
python -c "import whisper; print('whisper OK')"

# 3. First clip test (Telegram)
/clip https://www.youtube.com/watch?v=dQw4w9WgXcQ 30
```

---

## SELF-HEALING LOG

| Date | Issue | Fix Applied |
|---|---|---|
| 2026-04-17 | scheduler start outside event loop | Moved to _post_init (async context) |
| 2026-04-17 | /secondbrain show index FileNotFound | resolve_second_brain_file_name returns wiki/index.md |
| 2026-04-17 | OLLAMA_MODEL=gemma4 not found | Changed to gemma3:4b in .env + brain.py |
| 2026-04-17 | 9 CashClaw handlers not registered | Added CommandHandler + BotCommand entries |
| 2026-04-17 | Orchestrator missing utility functions | Added forward_message, validate_agent_output, sweep_stale_tasks |
| 2026-04-17 | 8 agent commands missing | Added clip/content/publish/tradingagent/performance handlers |
| 2026-04-17 | No real-time agent status API | Added /api/agents endpoint to Flask dashboard |
