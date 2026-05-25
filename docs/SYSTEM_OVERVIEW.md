# OpenClaw — System Overview

**Version**: Production (paper trading)
**Last Updated**: 2026-05-25
**Branch**: `claude/blofin-trading-bot-dashboard-TUJBC`
**Railway URL**: `cryptobot-production-18e1.up.railway.app`
**Owner**: Ronnie Irizarry (ronsi95openclaw@gmail.com)

---

## Quick-Start for New Sessions

```bash
# 1. Verify compilation
python -m py_compile trading/cryptocom_bot.py runtime/telegram_bot.py trading/strategies.py

# 2. Check strategy weights (nothing below 0.3 unless disabled)
cat data/strategy_weights.json

# 3. Confirm capital state
cat data/capital_state.json

# 4. Confirm API running (if local)
curl http://localhost:8000/api/status | python3 -m json.tool

# 5. Check for 403 errors (API key / IP issue)
cat data/logs/server.log | grep 403 | tail -20
```

**Current balance**: ~$295.30 | **Return**: +201% | **State**: SAFE | **Demo mode**: true

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        COGNITIVE LAYER                                      │
│                                                                             │
│   Claude Opus 4.7        Claude Haiku 4.5      Qwen2.5:14b (QUIN)          │
│   (nightly analyst)      (complex Telegram)    (per-scan gate, Ollama)     │
│   claude_analyst.py      core/brain.py         quin_orchestrator.py        │
│                                                                             │
│   Qwen3 (compression/structured)  Deepseek-coder (code)  Gemma3 (utils)   │
│   qwen_compressor.py               core/brain.py MODEL_REGISTRY            │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                                    │
│                                                                             │
│   RuntimeOrchestrator (runtime/orchestrator.py)                            │
│     Authority hierarchy (strictest to least):                               │
│     1. Kill switch / Emergency halt    ← supreme                            │
│     2. CapitalPreservationEngine       ← authoritative                      │
│     3. IntentPipeline (5 gates)        ← authoritative                      │
│     4. SkillClock (10 skills)          ← structured cognition               │
│     5. QUIN LLM gate                  ← advisory                           │
│     6. RufloAdvisor (HNSW memory)     ← advisory only                      │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                      SKILL CLOCK (10 skills, every scan tick)               │
│                                                                             │
│  [1] Market Data  →  [2] Regime Detection  →  [3] Signal Generation        │
│       ↓                                                                     │
│  [4] Risk/Capital  →  [5] Execution Decision  →  [6] Order Management      │
│       ↓                                                                     │
│  [7] Reconciliation  →  [8] Telemetry  →  [9] Learning/Drift               │
│       ↓                                                                     │
│  [10] Governance/Audit                                                      │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                       INTENT PIPELINE (5 gates)                             │
│                                                                             │
│  Gate 1: Schema validation  →  Gate 2: Staleness (90s TTL)                 │
│  Gate 3: Deduplication      →  Gate 4: Regime compatibility                 │
│  Gate 5: Capital preservation scalar (SAFE/DEFENSIVE/CRITICAL/HALT)        │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                       TRADING ENGINE                                        │
│                                                                             │
│   CryptoComBot              6 Strategies (self-learning weights)             │
│   trading/cryptocom_bot.py  trading/strategies.py                           │
│   60s scan loop             EMA_CROSS(0.4x) RSI(1.0x) BREAKOUT(1.0x)      │
│   SYMBOLS: BTC/ETH/SOL      BOLLINGER(1.0x) DCA(1.7x) TREND(0.3x)         │
│                                                                             │
│   Executor (live only)      Exchange (REST + MCP)                           │
│   trading/executor.py       trading/exchange.py                             │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                       MEMORY / PERSISTENCE                                  │
│                                                                             │
│   Ruflo HNSW (Node.js MCP)   data/response_cache.json (1h TTL, 200 max)   │
│   runtime/ruflo_bridge.py    data/replay_journal.jsonl (append-only)       │
│   ~210 MCP tools             data/quin_decisions.jsonl                     │
│   LOCAL ONLY — fails cloud   data/strategy_weights.json (self-learning)    │
│                              data/optimization/analysis_*.json (Opus)      │
│                                                                             │
│   Obsidian Vault: ~/AI-Operating-System-Vault/   [WRITES BROKEN]           │
│   ~/ai-system path missing → all vault writes silently fail                │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────────┐
│                      REPORTING / ALERTING                                   │
│                                                                             │
│   Telegram Bot (runtime/telegram_bot.py)    — two-way commands              │
│   Telegram Alerts (runtime/telegram_alerts.py) — outbound events           │
│   Google Sheets (reporting/google_sheets.py) — 5 tabs                      │
│   Dashboard API (dashboard/api/server.py) — FastAPI :8000, 25+ endpoints  │
│   React UI (dashboard/web/) — Next.js :3000 — NOT deployed to Railway      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Inventory

### Core Python Modules (200+)

| Module | File | Role | Auth Level |
|--------|------|------|------------|
| RuntimeOrchestrator | `runtime/orchestrator.py` (451 lines) | Central coordinator, wires all subsystems | Authoritative |
| CryptoComBot | `trading/cryptocom_bot.py` (1602 lines) | Main bot, 60s scan loop | Authoritative |
| IntentPipeline | `runtime/intent_pipeline.py` (185 lines) | 5-gate validation | Authoritative |
| CapitalPreservationEngine | `risk/capital_preservation.py` | SAFE/DEFENSIVE/CRITICAL/HALT state machine | Authoritative |
| SkillClock | `runtime/skill_clock.py` (483 lines) | 10-skill deterministic pipeline | Structural |
| QuinOrchestrator | `runtime/quin_orchestrator.py` | LLM signal gate, qwen2.5:14b | Advisory |
| RufloAdvisor | `runtime/ruflo_agent.py` | HNSW memory wrapper | Advisory only |
| RufloBridge | `runtime/ruflo_bridge.py` | MCP JSON-RPC to Node.js subprocess | Advisory only |
| HybridBrain | `core/brain.py` (477 lines) | Model routing, complexity detection | Infrastructure |
| ClaudeAnalyst | `runtime/claude_analyst.py` | Nightly Opus strategy analysis | Advisory |
| QwenCompressor | `runtime/qwen_compressor.py` | Per-trade lesson compression | Infrastructure |
| GoalTracker | `runtime/goal_tracker.py` | $98 → $50K milestone tracking | Observational |
| WeightScheduler | `runtime/weight_scheduler.py` | WeightApplicationDaemon, midnight | Infrastructure |
| SurvivabilityEngine | `runtime/survivability.py` | 0-100 health score, 8 subsystems | Observational |
| ReplayJournal | `runtime/replay_journal.py` | Append-only decision audit log | Infrastructure |
| IntegrityMonitor | `runtime/integrity_monitor.py` | Cross-validation checks | Observational |
| StrategyGovernance | `runtime/strategy_governance.py` | Strategy lifecycle management | Governance |
| BalanceFeedDaemon | `runtime/balance_feed.py` | Real exchange balance → CapitalEngine | Infrastructure |
| EventStore | `runtime/event_store.py` | Event sourcing | Infrastructure |
| SnapshotDaemon | `runtime/snapshot_daemon.py` | Periodic state snapshots | Infrastructure |
| ExecutionEngine | `trading/executor.py` | Live order placement (DEMO_MODE=true) | Execution |
| ExchangeAdapter | `trading/exchange.py` | Crypto.com REST + MCP market data | I/O |
| DashboardAPI | `dashboard/api/server.py` (684 lines) | FastAPI + WebSocket :8000 | Interface |
| TelegramBot | `runtime/telegram_bot.py` | Two-way command bot | Interface |
| TelegramAlerts | `runtime/telegram_alerts.py` | Outbound alerts | Interface |

### Research Modules

| Directory | Contents |
|-----------|----------|
| `research/backtesting/` | Backtesting engine |
| `research/montecarlo/` | Monte Carlo simulation |
| `research/optimization/` | Strategy optimization |
| `research/regimes/` | Regime classification |
| `research/walkforward/` | Walk-forward validation |
| `research/lifecycle/` | Strategy lifecycle |

### Governance / Security / Exchange

| Directory | Key Files |
|-----------|-----------|
| `governance/` | `approvals.py`, `emergency_controls.py`, `permissions.py`, `operator_controls.py` |
| `security/` | `api_firewall.py`, `auth.py`, `intrusion_detection.py`, `whitelist.py` |
| `exchange/` | `smart_router.py`, `venue_scoring.py`, `execution_quality.py`, `latency_tracker.py` |
| `system/` | `gpu_monitor.py`, `inference_scheduler.py`, `thermal_guard.py`, `workload_balancer.py` |

---

## Data Flows

### Per-Scan Flow (every 30-60 seconds)

```
CryptoComBot._scan()
  → SkillClock.tick()
      Skill 1: Fetch market data (BTC/ETH/SOL candles, price, orderbook)
      Skill 2: RegimeClassifier → TRENDING_BULL / TRENDING_BEAR / RANGING / UNKNOWN
      Skill 3: Run 6 strategies → list[StrategySignal]
      Skill 4: CapitalEngine.get_risk_scalar() → 0.0-1.0
      Skill 5: Pick best signal, build execution plan
      Skill 6: Check open positions for SL/TP/DCA/exit conditions
      Skill 7: Reconcile positions with exchange
      Skill 8: Emit telemetry (metrics, latency, health)
      Skill 9: Update weights, detect drift
      Skill 10: Governance policy check, audit log
  → QuinOrchestrator.decide(ctx)
      Primary: qwen2.5:14b via Ollama (local) or OpenRouter (cloud)
      Fallback: deterministic rule-based resolver
      Output: QuinDecision {action: TRADE|HOLD|SCALE_DOWN|EMERGENCY_HALT}
  → RuntimeOrchestrator.process_signal()
      → IntentPipeline.validate(intent)
          Gate 1: Schema bounds
          Gate 2: Staleness (expires 90s after generation)
          Gate 3: Deduplication (same symbol/strategy/action within TTL)
          Gate 4: Regime compatibility (TREND_FOLLOW blocked in TRENDING_BULL)
          Gate 5: Capital scalar from CapitalPreservationEngine
      → If approved: Executor.place_order() [DEMO_MODE=true → simulated]
  → ReplayJournal.append(decision)
  → TelegramAlerts.send() [if trade/milestone/halt]
```

### Nightly Analysis Flow (midnight UTC)

```
WeightApplicationDaemon wakes at 00:00 UTC
  → ClaudeAnalyst.run(trade_outcomes.jsonl)
      → Claude Opus 4.7 API call
      → Returns: {weight_adjustments, blocked_strategies, sl_recommendations}
      → Writes: data/optimization/analysis_<ts>.json
  → WeightApplicationDaemon applies weight_adjustments
      → Reads data/strategy_weights.json
      → Clamps weights: [0.1, 2.0]
      → Snapshots prior weights to data/weight_snapshots/
      → Appends to data/weight_adjustments_audit.jsonl
  → QwenCompressor generates per-trade lesson (qwen3 local)
```

---

## Deployment State

### Railway (cloud)

| Item | Status |
|------|--------|
| Branch | `claude/blofin-trading-bot-dashboard-TUJBC` |
| Builder | DOCKERFILE (python:3.11-slim) |
| Start command | `python main.py` |
| Port | `$PORT` (default 8000) |
| API network | BLOCKED — cannot reach api.crypto.com or api.telegram.org |
| Crypto data | Falls back to `_fake_candles()` simulation |
| Telegram | Inoperative in cloud |
| Ruflo HNSW | Inoperative in cloud (Node.js subprocess fails) |
| Ollama | Inoperative in cloud — OpenRouter fallback active |
| Dashboard web | Next.js NOT deployed (Railway only runs Python backend) |

### Local Machine

| Item | Status |
|------|--------|
| IP | 166.198.250.23 (whitelisted on Crypto.com API key) |
| Crypto.com API | Functional (paper trades) |
| Telegram | Functional |
| Ollama | qwen2.5:14b, qwen3, deepseek-coder, gemma3 |
| Ruflo HNSW | Functional when Node.js available |
| Obsidian vault | ~/AI-Operating-System-Vault/ exists, BUT ~/ai-system missing |

### Process Model (main.py)

```python
# main.py boots two services in one process:
Thread(target=_start_api_server)  # uvicorn :8000 — daemon thread
CryptoComBot.start()               # scan loop — foreground
```

### Background Daemons (start with bot)

| Daemon | Module | Purpose |
|--------|--------|---------|
| BalanceFeedDaemon | `runtime/balance_feed.py` | Real balance → CapitalEngine |
| WeightApplicationDaemon | `runtime/weight_scheduler.py` | Midnight weight application |
| TelegramCommandBot | `runtime/telegram_bot.py` | `/status /trades /goal /balance /weights /help /restart` |
| SnapshotDaemon | `runtime/snapshot_daemon.py` | Periodic state snapshots |
| ReconciliationScheduler | `runtime/reconciliation.py` | Position reconciliation |
| DriftDetector | `runtime/drift_detector.py` | Weight/performance drift |
| WebSocket Guardian | `runtime/ws_guardian.py` | WS connection health |
| ContinuousIntegrityMonitor | `runtime/integrity_monitor.py` | Cross-validation |
| ExecutionAnalytics | `runtime/execution_analytics.py` | p50/p95/p99 latency |

---

## Current Capabilities

### Strategy Performance (data/strategy_weights.json, 2026-05-25)

| Strategy | Weight | Trades | Wins | Losses | Win Rate |
|----------|--------|--------|------|--------|----------|
| DCA | 1.7x | 0 | 0 | 0 | n/a (new) |
| RSI_MEAN_REVERT | 1.0x | 0 | 0 | 0 | n/a (new) |
| BREAKOUT | 1.0x | 0 | 0 | 0 | n/a (new) |
| BOLLINGER_BAND | 1.0x | 2 | 1 | 1 | 50% |
| EMA_CROSS | 0.4x | 25 | 13 | 12 | 52% |
| TREND_FOLLOW | 0.3x | 4 | 1 | 3 | 25% |

### Capital State (data/capital_state.json)

```json
{
  "state": "SAFE",
  "alltime_peak": 295.30,
  "loss_streak": 0
}
```

### Goal Progress (data/goal_tracker.json)

- Starting balance: $98.00
- Current balance: $295.30
- Return: +201%
- Milestones hit: [$200]
- Target: $50,000
- Multiplier needed: ~169x remaining

### Dashboard Endpoints (25+)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Full bot snapshot |
| `/api/positions` | GET | Open positions |
| `/api/trades` | GET | Last 50 trades |
| `/api/weights` | GET | Strategy weights + stats |
| `/api/health` | GET | Capability matrix (31 items) |
| `/api/analysis` | GET | Latest Claude Opus report |
| `/api/goal` | GET | GoalProgress: balance, milestones, ETA |
| `/api/skill-clock` | GET | SkillClock 10-skill status |
| `/api/quin` | GET | QUIN orchestrator status |
| `/api/bot/start` | POST | Start the bot |
| `/api/bot/stop` | POST | Stop the bot |
| `/api/bot/configure` | POST | Update settings |
| `/ws` | WebSocket | Real-time event stream (5 channels) |

---

## Key Data Files

| File | Purpose | Format |
|------|---------|--------|
| `data/cryptocom_state.json` | Live bot state (starting_balance: 98.0) | JSON |
| `data/strategy_weights.json` | 6 strategies with self-learning weights [0.1-2.0] | JSON |
| `data/capital_state.json` | SAFE/DEFENSIVE/CRITICAL/HALT + alltime_peak | JSON |
| `data/goal_tracker.json` | $98→$50K progress persistence | JSON |
| `data/skill_clock_audit.jsonl` | 10-skill pipeline per-tick audit | JSONL |
| `data/quin_decisions.jsonl` | QUIN gate decisions log (immutable) | JSONL |
| `data/logs/trade_outcomes.jsonl` | Closed trades (Claude Analyst reads this) | JSONL |
| `data/optimization/analysis_*.json` | Claude Opus daily reports | JSON |
| `data/replay_journal.jsonl` | Append-only decision audit log | JSONL |
| `data/response_cache.json` | 1-hour TTL response cache (max 200 entries) | JSON |
| `data/usage_stats.json` | Daily token counts per model | JSON |

---

## Known Gaps (Critical)

| # | Gap | Impact | Fix Location |
|---|-----|--------|--------------|
| 1 | `~/ai-system` missing → Obsidian vault writes silently fail | All trade journal, strategy evolution, daily notes lost | See `docs/OBSIDIAN_MEMORY_SYSTEM.md` |
| 2 | No Ruflo HNSW in cloud (Node.js subprocess) | Pre-trade memory advisory disabled on Railway | Cloud alternative needed |
| 3 | No Claude Sonnet routing tier | Haiku handles everything; Opus only at midnight | `core/brain.py` — add Sonnet tier |
| 4 | Memory fragmented (4 stores, no unified retrieval) | No cross-store query, Obsidian invisible to Claude | Unified memory API needed |
| 5 | No Obsidian context injected into Claude calls | Claude Opus analyzes without vault knowledge | Memory injection design |
| 6 | Dashboard Next.js not deployed to Railway | No web UI in cloud | Railway static/separate service |
| 7 | system/* modules (GPU, thermal) not cloud-compatible | Resource monitoring dead in cloud | Expected; document clearly |
| 8 | QUIN cannot escalate to Claude Opus | High-stakes decisions get qwen2.5:14b, not Opus | Add escalation path in quin_orchestrator.py |

---

## Audit Phase Summary

The system has been through 9 audit phases (see `audit_reports_phase1/` through `audit_reports_phase9/`).

**Phase 9 composite score: 100/100 (Supervised Live Ready)**
**Survivability in cloud: ~66/100 (DEGRADED — expected, not a bug)**

| Category | Score |
|----------|-------|
| Event lifecycle | 10/10 |
| Exchange integration | 10/10 |
| Capital protection | 10/10 |
| WebSocket reliability | 10/10 |
| Snapshot & recovery | 10/10 |
| Strategy governance | 9/10 |
| Execution quality | 10/10 |
| Research / alpha | 10/10 |
| Operational tooling | 10/10 |
| CI/CD & deployment | 10/10 |
| Security | 10/10 |
| Observability | 10/10 |
| Test coverage | 10/10 (395 tests) |

---

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `DEMO_MODE` | `true` = paper trading, `false` = live | Yes |
| `ANTHROPIC_API_KEY` | Claude API (Haiku + Opus calls) | Yes |
| `OPENROUTER_API_KEY` | Cloud LLM fallback (Railway) | Railway |
| `TELEGRAM_BOT_TOKEN` | `8647354078:AAEb...` | Yes |
| `TELEGRAM_CHAT_ID` | `6082698835` (Ronnie) | Yes |
| `CRYPTOCOM_API_KEY` | Exchange API key | Live only |
| `CRYPTOCOM_SECRET` | Exchange secret | Live only |
| `GOOGLE_CREDS_JSON` | Google Sheets credentials | Optional |
| `PORT` | API server port (default 8000) | Railway |
| `DASHBOARD_TOKEN` | Token for dashboard API auth | Optional |
| `OLLAMA_MODEL` | Override default Ollama model | Optional |
| `QUIN_MODEL` | Override QUIN model (default qwen2.5:14b) | Optional |
| `RUFLO_ENABLED` | Enable Ruflo HNSW (default: env-based) | Optional |
| `USE_CLAUDE_API` | Enable Claude API calls (default: true) | Optional |
| `MAX_TOKENS_PER_RESPONSE` | Cap Claude response tokens (default: 500) | Optional |
| `OPENROUTER_SITE_URL` | OpenRouter HTTP-Referer header | Optional |
| `OPENROUTER_SITE_NAME` | OpenRouter X-Title header | Optional |
