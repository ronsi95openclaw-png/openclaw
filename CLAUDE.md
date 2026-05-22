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

### Claude responsibilities
- Architecture, planning, systems reasoning
- Governance and optimization review
- Strategic thinking and interface contracts
- Replay review and decomposition planning

### What Claude does NOT do
- Unrestricted autonomous deployment
- Governance authority decisions
- Bypassing validation pipelines

### Per-project AI governance structure
Every project must contain:
```
.ai/
├── architecture/
├── replay/
├── validation/
├── governance/
├── prompts/
├── memory/
├── metrics/
└── docs/
```

### Obsidian Knowledge Vault
Central long-term memory at `~/AI-Operating-System-Vault/`
All major architecture decisions, incidents, optimizations, strategy evolution,
and replay traces are documented there automatically.

Vault structure:
```
~/AI-Operating-System-Vault/
├── 00_Dashboard/       — system overview, active agents, health metrics
├── 01_Architecture/    — architecture docs, interface contracts
├── 02_Projects/        — per-project knowledge
├── 03_Agents/          — agent configs and routing rules
├── 04_Research/        — market + technical research
├── 05_Trading/         — trading system docs, position logs
├── 06_Strategies/      — strategy evolution, performance history
├── 07_Optimization/    — Claude Opus reports, weight adjustments
├── 08_Logs/            — operational logs
├── 09_Replay/          — replay traces and analysis
├── 10_Governance/      — governance decisions, audit trail
├── 11_Security/        — security reviews, incident reports
├── 12_Deployments/     — deployment notes, changelogs
├── 13_Memory/          — AI reasoning summaries, embeddings index
├── 14_Prompts/         — prompt library
├── 15_Workflows/       — Ruflo workflow definitions
├── 16_Documentation/   — generated API + architecture docs
├── 17_Postmortems/     — incident postmortems
├── 18_Roadmaps/        — evolution roadmaps
├── 19_Resources/       — references, research papers
└── 20_Daily_Notes/     — daily operational notes
```

### Standard workflow (every task)
1. Claude → architecture decision
2. Ruflo → orchestration and decomposition
3. Local LLM → implementation
4. Validation → lint + type + test + replay
5. Obsidian → automatic documentation
6. Claude → review
7. Human approval → merge + deploy

### Resource management rules
- GPU-aware scheduling — never saturate VRAM
- Thermal throttling — monitor CPU/GPU temp
- Inference queueing — one model at a time unless GPU allows concurrency
- Degraded mode — fall back gracefully if local models unavailable

### Security rules
- Encrypted secrets, local-only access
- Audit logging for all AI decisions
- Prompt injection protection at all input boundaries
- Sandbox execution for untrusted code
- Never expose exchange keys, DB credentials, or governance controls

### Validation pipeline (mandatory before merge)
- Formatting → linting → typing → compilation → tests → replay → security → performance
