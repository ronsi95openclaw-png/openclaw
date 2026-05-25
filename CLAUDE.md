# OpenClaw — Claude Operating Instructions

## What this project is
Crypto trading bot running on Crypto.com perpetual futures (BTCUSD-PERP, ETHUSD-PERP, SOLUSD-PERP) with 3× leverage. Currently in **paper/demo mode only**. Owner: Ronnie Irizarry.

## Current status (last updated 2026-05-25)
- DEMO_MODE=true — paper trading, no real orders
- Bot running 24/7 on Railway (cloud) — `cryptobot-production-18e1.up.railway.app`
- Starting balance: $98.00 | Current balance: ~$295.30 | Return: +201%
- Goal: $98 → $50,000 (8 milestones)
- IP 166.198.250.23 whitelisted on Crypto.com API key
- **Cloud environment cannot reach api.crypto.com or api.telegram.org** — network blocked. Bot runs with real data only on local machine.
- Railway deployment: ACTIVE on branch `claude/blofin-trading-bot-dashboard-TUJBC`
- OpenRouter configured as cloud LLM fallback (Railway env var: OPENROUTER_API_KEY)

## Architecture (top → bottom)
```
Qwen qwen2.5:14b  (runtime/qwen_compressor.py)    — per-trade lesson, Ollama local
Claude Opus       (runtime/claude_analyst.py)      — nightly strategy analysis
QUIN              (runtime/quin_orchestrator.py)   — LLM signal gate (rule-based fallback)
Skill Clock       (runtime/skill_clock.py)         — 10-skill deterministic pipeline per scan
Goal Tracker      (runtime/goal_tracker.py)        — $98→$50K milestones + ETA
Ruflo HNSW        (runtime/ruflo_agent.py)         — pre-trade memory advisory
Intent Pipeline   (runtime/intent_pipeline.py)     — schema + regime + capital gate
Capital Engine    (risk/capital_preservation.py)   — SAFE/DEFENSIVE/CRITICAL/HALT
CryptoComBot      (trading/cryptocom_bot.py)        — main bot, 60s scan loop
Executor          (trading/executor.py)             — order placement (live only)
Crypto.com API    (trading/exchange.py)             — REST + MCP market data
Google Sheets     (reporting/google_sheets.py)      — 5 tabs: Signals/Trades/Regime/Daily/Claude Analysis
Telegram Bot      (runtime/telegram_bot.py)         — two-way command bot (polling)
Telegram Alerts   (runtime/telegram_alerts.py)      — outbound trade/milestone/halt alerts
Dashboard API     (dashboard/api/server.py)         — FastAPI + WebSocket (port 8000)
React UI          (dashboard/web/)                  — Next.js control center (port 3000)
HaulYall Bot      (~/haulyall/bot.py)               — separate hauling job tracker bot
```

## Telegram setup
- **@Ronsi95openclawbot** — token `8647354078:AAEbBwS6pqJ2_H6tXVWFXzj3mLcEO6s6ptk`
- **Chat ID**: `6082698835` (Ronnie)
- Commands: `/status` `/trades` `/goal` `/balance` `/weights` `/help`
- Alerts: trade open/close (with balance), milestone hit, emergency halt, daily midnight report
- HaulYall bot uses separate token `8831940231:AAGUCwYSiUZsQT8xIYYj7ffRPUDUBem73po`

## 6 Active strategies
| Strategy | Weight | Notes |
|---|---|---|
| DCA | 1.7× | NEW — buys 20-period session lows, stronger on red days >2% |
| RSI_MEAN_REVERT | 1.0× | Thresholds 32/68 (widened from 27/73) |
| BOLLINGER_BAND | 1.0× | 50% WR — working well |
| BREAKOUT | 1.0× | Active |
| EMA_CROSS | 0.4× | Penalized — 52% WR, 25 trades |
| TREND_FOLLOW | 0.3× | Penalized + BLOCKED in TRENDING_BULL (0% WR there) |

## Data-driven trading adjustments made
- TREND_FOLLOW forbidden in TRENDING_BULL regime (was 0/4 wins)
- TREND_FOLLOW SL hard-capped at 5% max (ATR×2 had no ceiling — caused -$424 loss)
- TREND_FOLLOW EMA gap threshold raised 0.10% → 2.0% (require established trend)
- DCA strategy ported from cryptobot repo and integrated
- RSI thresholds widened to 32/68 to match proven signal history

## Key files
- `.env` — API keys (never commit, never log)
- `credentials.json` — Google service account (never commit)
- `data/cryptocom_state.json` — live bot state (starting_balance: 98.0)
- `data/strategy_weights.json` — 6 strategies with self-learning weights
- `data/capital_state.json` — SAFE, alltime_peak: 295.30
- `data/goal_tracker.json` — $98→$50K progress persistence
- `data/skill_clock_audit.jsonl` — 10-skill pipeline audit
- `data/quin_decisions.jsonl` — QUIN gate decisions log
- `data/logs/trade_outcomes.jsonl` — closed trades (Claude Analyst reads this)
- `data/optimization/analysis_*.json` — Claude Opus daily reports
- `data/replay_journal.jsonl` — append-only decision audit log

## API endpoints added (dashboard/api/server.py)
- `GET /api/goal` — GoalProgress: balance, milestones, ETA, multiplier
- `GET /api/skill-clock` — SkillClock 10-skill pipeline status
- `GET /api/quin` — QUIN orchestrator status

## Hard rules
- NEVER commit `.env`, `credentials.json`, or `setup.sh`
- NEVER set DEMO_MODE=false without explicit user instruction
- NEVER bypass the IntentPipeline gate
- NEVER push to main branch
- Always develop on branch: `claude/blofin-trading-bot-dashboard-TUJBC`
- Always push after completing a task

## How to launch (local machine)
```bash
# Pull latest
cd ~/openclaw
git pull origin claude/blofin-trading-bot-dashboard-TUJBC

# Backend (bot + API)
python dashboard/api/server.py &
sleep 10
curl -X POST http://localhost:8000/api/bot/start

# Frontend (dashboard)
cd dashboard/web && npm install && npm run dev

# HaulYall bot (separate terminal)
cd ~/haulyall && python bot.py

# Dashboard opens at http://localhost:3000
# API health: http://localhost:8000/api/status
```

## What to check at start of every session
1. `python -m py_compile trading/cryptocom_bot.py runtime/telegram_bot.py trading/strategies.py`
2. `cat data/strategy_weights.json` — check for any strategy below weight 0.3
3. `cat data/capital_state.json` — confirm state is SAFE, alltime_peak matches balance
4. `curl http://localhost:8000/api/status | python3 -m json.tool` — confirm running=true
5. Check `data/logs/server.log` for 403 errors (means API key or IP issue)

## Known issues / cloud environment notes
- Cloud env network blocks `api.crypto.com` and `api.telegram.org`
- Bot falls back to `_fake_candles()` simulation in cloud — NOT real trading
- Survivability shows DEGRADED (66/100) in cloud — expected, not a bug
- Real bot must run on local machine (IP 166.198.250.23 whitelisted)

## Cryptobot repo (ronsi95openclaw-png/cryptobot)
- Original trading bot Ronnie was running before OpenClaw
- Uses RANDOM win/loss simulation in demo mode (explains $7.3M fake balance)
- Strategies ported into OpenClaw: DCA (1.7×), RSI_MEAN (32/68 thresholds)
- Old bot should be STOPPED on local machine — OpenClaw replaces it entirely

## Improvement backlog (priority order)
1. ~~Block TREND_FOLLOW in UNKNOWN/BULL regime~~ ✅ DONE
2. ~~Telegram two-way command bot~~ ✅ DONE
3. ~~DCA strategy from cryptobot~~ ✅ DONE
4. ~~GoalTracker $98→$50K~~ ✅ DONE
5. ~~Skill Clock 10-skill pipeline~~ ✅ DONE
6. ~~Auto-apply Claude Opus weight_adjustments to strategy_weights.json at midnight~~ ✅ DONE (runtime/weight_scheduler.py — WeightApplicationDaemon)
7. ~~Auto-disable strategies with weight < 0.3 for 20+ trades~~ ✅ DONE (_auto_disable_weak_strategies in cryptocom_bot.py)
8. ~~Feed real Crypto.com balance into CapitalPreservationEngine~~ ✅ DONE (runtime/balance_feed.py + live_balance_guardian.py; advisory in DEMO_MODE)
9. ~~Add /restart command to Telegram bot~~ ✅ DONE (runtime/telegram_bot.py)
10. ~~Dashboard GoalTracker component~~ ✅ DONE
11. Railway deployment — single-process main.py, DOCKERFILE builder, OpenRouter LLM fallback ✅ DONE

---

## Global AI Engineering Operating System (all projects)

This machine operates as a **local-first AI engineering + knowledge OS**.
Every project inherits this philosophy.

### AI Hierarchy
```
Claude Opus 4.6          — architecture, governance, strategic analysis
↓
Ruflo Orchestrator       — workflow decomposition, agent routing, execution coordination
↓
Local LLM Pool (Ollama)  — implementation, coding, refactors, summarization, testing
  ├── qwen2.5-coder:14b  — code implementation, tests, APIs
  ├── qwen3:14b          — structured outputs, operational reasoning
  ├── deepseek-coder     — repo-wide refactors, migrations
  └── gemma3             — utilities, scripting, lightweight tasks
↓
Validation Layer         — lint, type, compile, test, replay, security scan
↓
Human Approval
```

### Security rules
- Encrypted secrets, local-only access
- Audit logging for all AI decisions
- Prompt injection protection at all input boundaries
- Sandbox execution for untrusted code
- Never expose exchange keys, DB credentials, or governance controls

### Validation pipeline (mandatory before merge)
- Formatting → linting → typing → compilation → tests → replay → security → performance
