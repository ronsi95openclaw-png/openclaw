# OpenClaw — Claude Operating Instructions

## What this project is
Crypto trading bot running on Crypto.com perpetual futures (BTCUSD-PERP, ETHUSD-PERP, SOLUSD-PERP) with 3× leverage. Currently in **paper/demo mode only**. Owner: Ronnie.

## Current status
- DEMO_MODE=true — paper trading, no real orders
- Not live yet. Do not change DEMO_MODE=false without explicit instruction
- IP 166.198.250.23 whitelisted on Crypto.com API key

## Architecture (top → bottom)
```
Qwen qwen2.5:14b (runtime/qwen_compressor.py)   — per-trade lesson, Ollama local
Claude Opus      (runtime/claude_analyst.py)     — nightly strategy analysis
Ruflo HNSW       (runtime/ruflo_agent.py)        — pre-trade memory advisory
Intent Pipeline  (runtime/intent_pipeline.py)    — schema + regime + capital gate
Capital Engine   (risk/capital_preservation.py)  — SAFE/DEFENSIVE/CRITICAL/HALT
CryptoComBot     (trading/cryptocom_bot.py)       — main bot, 30s scan loop
Executor         (trading/executor.py)            — order placement (live only)
Crypto.com API   (trading/exchange.py)            — REST + MCP market data
Google Sheets    (reporting/google_sheets.py)     — 5 tabs: Signals/Trades/Regime/Daily/Claude Analysis
Dashboard API    (dashboard/api/server.py)        — FastAPI + WebSocket (port 8000)
React UI         (dashboard/web/)                 — Next.js control center (port 3000)
```

## Key files
- `.env` — API keys (never commit, never log)
- `credentials.json` — Google service account (never commit)
- `data/cryptocom_state.json` — live bot state
- `data/strategy_weights.json` — self-learning strategy weights
- `data/logs/trade_outcomes.jsonl` — closed trades (Claude Analyst reads this)
- `data/optimization/analysis_*.json` — Claude Opus daily reports
- `data/replay_journal.jsonl` — append-only decision audit log

## Hard rules
- NEVER commit `.env`, `credentials.json`, or `setup.sh`
- NEVER set DEMO_MODE=false without explicit user instruction
- NEVER bypass the IntentPipeline gate
- NEVER push to main branch
- Always develop on branch: `claude/blofin-trading-bot-dashboard-TUJBC`
- Always push after completing a task

## How to launch
```bash
# Backend (bot + API)
python dashboard/api/server.py

# Frontend (dashboard)
cd dashboard/web && npm install && npm run dev

# Dashboard opens at http://localhost:3000
```

## What I check at the start of every session
1. Run capability matrix: `python -m runtime.capability_matrix` — expect 29+ OK
2. Check `data/strategy_weights.json` for any strategy below weight 0.5
3. Read latest `data/optimization/analysis_*.json` if it exists
4. Confirm no syntax errors: `python -m py_compile trading/cryptocom_bot.py`

## Improvement backlog (priority order)
1. Block TREND_FOLLOW in UNKNOWN regime — historically 0% WR
2. Auto-apply Claude Opus weight_adjustments to strategy_weights.json at midnight
3. Dynamic scan interval — slow in ranging, fast in trending
4. Telegram alerts — wire TELEGRAM_BOT_TOKEN for trade/HALT notifications
5. Auto-disable strategies with weight < 0.3 for 20+ trades
6. Feed real Crypto.com balance into CapitalPreservationEngine
