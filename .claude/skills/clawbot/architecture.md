# ClawBot — Architecture Reference

## Module Map

```
openclaw/
├── main.py                          # Entry point: API + bot + relay
├── settings.py                      # Global params (DEMO_MODE, DEMO_SLIPPAGE_PCT, etc.)
│
├── trading/
│   ├── cryptocom_bot.py             # CryptoComBot — main scan loop (60s)
│   ├── strategies.py                # 6 strategy signal generators
│   ├── executor.py                  # Order placement (demo fill + slippage)
│   ├── exchange.py                  # Crypto.com REST API client
│   └── cryptocom_mcp_bridge.py      # MCP → internal candle format normalizer
│
├── runtime/
│   ├── intent_pipeline.py           # 5-gate safety filter (schema→stale→dedup→regime→capital)
│   ├── orchestrator.py              # Central coordinator (authority hierarchy)
│   ├── quin_orchestrator.py         # LLM signal gate (Ollama / OpenRouter fallback)
│   ├── skill_clock.py               # 10-skill deterministic pipeline
│   ├── goal_tracker.py              # $98→$50K milestones + ETA
│   ├── ruflo_agent.py               # Pre-trade memory advisory (HNSW)
│   ├── ruflo_bridge.py              # Pure Python hnswlib wrapper
│   ├── telegram_bot.py              # Two-way command bot (polling or webhook)
│   ├── telegram_alerts.py           # Outbound fire-and-forget alerts
│   ├── telegram_relay.py            # Supabase outbox → Telegram relay daemon
│   ├── morning_briefing.py          # MorningBriefingDaemon (08:00 UTC)
│   │                                # MidnightReportDaemon (00:00 UTC)
│   │                                # HeartbeatDaemon (every 4h)
│   ├── live_mode_gate.py            # Live mode eligibility checks + /livecheck bars
│   ├── weight_scheduler.py          # Midnight weight application daemon
│   ├── balance_feed.py              # Periodic live balance feed
│   ├── live_balance_guardian.py     # Cross-validates exchange vs internal balance
│   ├── scan_interval_engine.py      # Dynamic scan interval
│   ├── capital_preservation.py      # SAFE/DEFENSIVE/CRITICAL/HALT state machine
│   └── survivability.py             # Operational health scoring (0–100)
│
├── risk/
│   ├── capital_preservation.py      # Authoritative capital state machine
│   └── portfolio_risk.py            # Cross-position exposure aggregation
│
├── infra/
│   ├── state_store.py               # Unified persistence (local JSON + Supabase)
│   └── supabase_client.py           # Supabase singleton (falls back to None gracefully)
│
├── dashboard/
│   └── api/
│       ├── server.py                # FastAPI app (port 8000) + webhook endpoint
│       ├── event_bus.py             # Thread-safe sync→async WebSocket bridge
│       └── routers/phase9.py        # v2 REST endpoints
│
├── research/
│   └── regimes/
│       ├── classifier.py            # RegimeClassifier → TRENDING_BULL etc.
│       └── strategy_compatibility.py # forbidden/supported regime matrix
│
└── data/
    ├── cryptocom_state.json          # Live bot state
    ├── capital_state.json            # Capital engine state
    ├── strategy_weights.json         # Adaptive strategy weights
    ├── goal_tracker.json             # Milestone progress
    ├── dca_state.json                # DCA cost basis per symbol
    └── logs/trade_outcomes.jsonl     # Closed trade log (used by live gate + Claude Analyst)
```

---

## BotState Fields (trading/cryptocom_bot.py)

```python
class BotState:
    demo_mode:        bool    = True
    risk_pct:         float   = 1.5      # % of balance per trade
    balance:          float   = 0.0      # live exchange balance
    starting_balance: float   = 98.0
    total_pnl:        float   = 0.0
    trades_today:     int     = 0
    total_trades:     int     = 0
    winning_trades:   int     = 0
    losing_trades:    int     = 0
    last_scan:        str     = ""
    scan_interval:    int     = 60
    status_msg:       str     = "Idle"
    open_positions:   list    = []       # list of position dicts
    trade_log:        list    = []       # last 50 closed trades
    execution_paused: bool    = False    # set by integrity check or /pause
    running:          bool    = False

# Balance derivation (when balance key missing from state):
balance = starting_balance + total_pnl
```

---

## Intent Pipeline — 5 Gates (runtime/intent_pipeline.py)

```
Gate 1: Schema / bounds
  - symbol in ALLOWED_SYMBOLS
  - action in {long, short, close}
  - confidence 0.0–1.0
  - size_pct 0.1–10.0

Gate 2: Staleness
  - signal timestamp < 90 seconds old

Gate 3: Deduplication (atomic, thread-safe)
  - same (symbol, strategy, action) not seen in last 90s

Gate 4: Regime compatibility
  - research/regimes/strategy_compatibility.py
  - TREND_FOLLOW forbidden in TRENDING_BULL (and 7 others)

Gate 5: Capital preservation scalar
  - SAFE: scalar=1.0 (full sizing)
  - DEFENSIVE: scalar=0.5
  - CRITICAL: scalar=0.25
  - EMERGENCY_HALT: scalar=0.0 (no trades)
```

---

## Trade Record Schema (data/logs/trade_outcomes.jsonl)

```json
{
  "id": "uuid",
  "symbol": "BTC_USDT",
  "strategy": "BOLLINGER_BAND",
  "side": "short",
  "action": "short",
  "entry_price": 76512.41,
  "exit_price": 75900.00,
  "sl": 77277.54,
  "tp": 74599.60,
  "size": 0.0039,
  "pnl": 4.23,
  "outcome": "win",
  "regime_label": "RANGING",
  "confidence": 0.82,
  "demo": true,
  "opened_at": "2026-05-27T23:24:20+00:00",
  "closed_at": "2026-05-27T23:45:00+00:00",
  "lesson": "QUIN lesson text"
}
```

---

## Ruflo HNSW Memory (runtime/ruflo_bridge.py)

```python
EMBEDDING_DIM = 384          # sentence-transformers/all-MiniLM-L6-v2
HNSW_M        = 16           # connections per node
HNSW_EF       = 200          # search candidates
MAX_MEMORIES  = 10_000
MEMORY_FILE   = "data/ruflo/memories.pkl"

# Each memory stores:
{
  "text": "lesson from trade",
  "embedding": [384 floats],
  "metadata": {"symbol": str, "strategy": str, "outcome": str, "pnl": float}
}
```

---

## Correct Startup Sequence (main.py)

```
1. Load .env
2. Windows fcntl shim (if sys.platform == 'win32')
3. Start API server in background thread (uvicorn port 8000)
   └── startup event: _register_telegram_webhook()
       └── local: deleteWebhook → long-poll mode
       └── Railway: setWebhook → webhook mode
4. Start Telegram relay daemon (if not RAILWAY_PUBLIC_URL)
5. sleep(3) — let uvicorn bind
6. startup_integrity_check() → ok=True required to not pause
7. CryptoComBot() init:
   └── loads state from local JSON → Supabase fallback
   └── starts all subsystems (orchestrator, reconciliation, etc.)
8. bot.start():
   └── starts all daemons (snapshot, integrity, balance_feed, weights)
   └── starts TelegramCommandBot (polling or webhook mode)
   └── starts MorningBriefing, MidnightReport, Heartbeat daemons
   └── alert_bot_started() → Telegram startup message
9. Main loop: monitor bot.is_running() every 60s, restart if stopped
```
