# ClawBot Skill — OpenClaw Crypto Trading Bot

Auto-loaded context for every ClawBot session. Pull reference files on demand.

---

## SYSTEM FACTS

| Key | Value |
|-----|-------|
| Project path | `/home/user/openclaw` (cloud) · `~/openclaw` (local) |
| Active branch | `claude/blofin-trading-bot-dashboard-TUJBC` |
| Railway URL | `https://cryptobot-production-18e1.up.railway.app` |
| Supabase project | `gotdcwcdcampwysydbzg` |
| Balance | $295.30 (starting $98, PnL +$197.30) |
| Goal | $98 → $50,000 (8 milestones) |
| Mode | DEMO_MODE=true — paper trading only |
| Exchange | Crypto.com perpetual futures (BTC/ETH/SOL) |
| Leverage | 3× |
| Bot token | `@Ronsi95openclawbot` — `8647354078:AAEb…` |
| Chat ID | `6082698835` (Ronnie) |
| Latest commit | `fbf632b` |

---

## HARD CONSTRAINTS — NEVER VIOLATE

```
NEVER set DEMO_MODE=false
NEVER commit .env, credentials.json, setup.sh
NEVER push to main branch
NEVER bypass the IntentPipeline gate
NEVER touch TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SUPABASE_URL,
       SUPABASE_ANON_KEY, CRYPTOCOM_API_KEY, CRYPTOCOM_SECRET
ALWAYS develop on: claude/blofin-trading-bot-dashboard-TUJBC
ALWAYS push after completing a task
```

---

## ARCHITECTURE (top → bottom)

```
Telegram Bot (runtime/telegram_bot.py)       ← commands in
Telegram Relay (runtime/telegram_relay.py)   ← replies out via Supabase
     ↓
QUIN Orchestrator (runtime/quin_orchestrator.py)  ← LLM signal gate
Skill Clock (runtime/skill_clock.py)              ← 10-skill pipeline
Goal Tracker (runtime/goal_tracker.py)            ← $98→$50K
Ruflo HNSW (runtime/ruflo_agent.py)               ← pre-trade memory
Intent Pipeline (runtime/intent_pipeline.py)      ← 5-gate safety
Capital Engine (risk/capital_preservation.py)     ← SAFE/DEFENSIVE/CRITICAL/HALT
     ↓
CryptoComBot (trading/cryptocom_bot.py)       ← main bot, 60s scan loop
Executor (trading/executor.py)               ← order placement
Exchange (trading/exchange.py)               ← REST + MCP market data
     ↓
Dashboard API (dashboard/api/server.py)      ← FastAPI port 8000
Supabase (infra/supabase_client.py)          ← cloud state persistence
```

---

## 14 TELEGRAM COMMANDS

| Command | Handler | What it returns |
|---------|---------|-----------------|
| `/status` | `_cmd_status` | balance, PnL, unrealised, win rate, capital state |
| `/trades` | `_cmd_trades` | today's closed trades + open positions |
| `/goal` | `_cmd_goal` | $295→$50K progress, milestones, ETA |
| `/balance` | `_cmd_balance` | balance breakdown |
| `/weights` | `_cmd_weights` | 6 strategies, weight, WR, trade count |
| `/livecheck` | `_cmd_livecheck` | ASCII progress bars for 4 live-mode gates |
| `/golive` | `_cmd_golive` | activate live mode (requires passphrase) |
| `/dca_status` | `_cmd_dca_status` | DCA portfolio cost basis per coin |
| `/briefing` | `_cmd_briefing` | send morning briefing now |
| `/pause` | `_cmd_pause` | pause trade execution |
| `/resume` | `_cmd_resume` | resume trade execution |
| `/halt` | `_cmd_halt` | capital halt status + release info |
| `/restart` | `_cmd_restart` | restart scan loop |
| `/help` | `_cmd_help` | all commands |

---

## 6 ACTIVE STRATEGIES

| Strategy | Weight | Notes |
|----------|--------|-------|
| DCA | tracked separately | in data/dca_state.json — not in weights engine |
| RSI_MEAN_REVERT | 1.0× | thresholds 32/68 |
| BOLLINGER_BAND | 1.0× | working well |
| BREAKOUT | 1.0× | active |
| EMA_CROSS | 0.8× | 25 trades |
| TREND_FOLLOW | 1.0× | FORBIDDEN in TRENDING_BULL regime |
| VWAP | 1.0× | active |

TREND_FOLLOW forbidden regimes: RANGING, MEAN_REVERTING, VOL_COMPRESSION,
PANIC, LIQUIDATION_CASCADE, LIQUIDITY_DROUGHT, UNKNOWN, **TRENDING_BULL**

---

## LIVE GATE REQUIREMENTS (4 checks)

| Check | Requirement | File |
|-------|-------------|------|
| Paper trades | ≥ 30 | data/logs/trade_outcomes.jsonl |
| Win rate | ≥ 54% | same file |
| Capital state | SAFE | data/capital_state.json |
| Slippage sim | DEMO_SLIPPAGE_PCT > 0 | settings.py |

Current status: 10/30 trades, ~30% WR → NOT eligible

---

## TELEGRAM RELAY ARCHITECTURE (Railway)

Railway IP is blocked by Telegram's Bot API allowlist.

```
User → Telegram → Railway /telegram/webhook → processes command
     → writes reply to Supabase telegram_outbox
     → local relay daemon polls outbox every 3s
     → sends reply via api.telegram.org (local IP is whitelisted)
     → User receives reply
```

Key env vars:
- `TELEGRAM_OUTBOX_MODE=supabase` (in railway.toml) → routes sendMessage to outbox
- `RAILWAY_PUBLIC_URL` set → skips getUpdates polling, uses webhook
- Local (no RAILWAY_PUBLIC_URL) → long-poll mode, sends direct

---

## SUPABASE TABLES

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `bot_state` | live bot state | balance, total_pnl, open_positions, trade_log |
| `capital_state` | capital engine state | state, alltime_peak, daily_drawdown |
| `strategy_weights` | adaptive weights | strategy, weight, win_rate, trades |
| `goal_tracker` | $98→$50K progress | milestones_hit, days_running |
| `trade_outcomes` | closed trade log | symbol, strategy, pnl, outcome, regime_label |
| `telegram_outbox` | reply relay queue | chat_id, text, parse_mode, sent_at, error |
| `telegram_updates` | inbound command audit | update_id, chat_id, text |
| `quin_decisions` | QUIN LLM decisions | action, confidence, reasoning, signal |

RLS: all tables enabled. telegram_outbox has relay_insert/select/update policies for anon role.

---

## QUICK DIAGNOSTICS

```bash
# Import chain (all 14 modules)
python -c "
mods=['settings','infra.state_store','risk.capital_preservation',
      'trading.cryptocom_bot','runtime.telegram_bot','runtime.telegram_relay',
      'runtime.morning_briefing','runtime.live_mode_gate','dashboard.api.server']
[(__import__(m), print('OK', m)) for m in mods]
"

# Current state
cat data/cryptocom_state.json | python3 -m json.tool
cat data/capital_state.json
cat data/strategy_weights.json | python3 -m json.tool

# Boot
python main.py

# Relay only (local, sends Supabase outbox → Telegram)
python runtime/telegram_relay.py
```

---

## KNOWN ISSUES (all fixed)

| Issue | Fix | Commit |
|-------|-----|--------|
| Railway IP blocks Telegram outbound | Supabase relay architecture | 7e938ed |
| 409 webhook conflict on local boot | auto-deleteWebhook on 409 | 46a5dbc |
| Startup integrity false-positive on Railway | _OUTCOMES_FILE.exists() guard | e6e7f59 |
| Balance showing $98 instead of $295 | derive balance = starting + pnl | earlier |
| Telegram commands silent (webhook+poll conflict) | skip poll when RAILWAY_PUBLIC_URL set | e6e7f59 |
| Webhook handler blocking (duplicate responses) | asyncio.create_task dispatch | 8619f37 |
| get_bot() double-init corrupting event store | reuse _cmd_bot._bot_ref | 8619f37 |
| /livecheck WR bar showed wrong % | _wr_bar() helper | 8619f37 |
| fcntl crash on Windows | sys.modules shim in main.py | fbf632b |
| Google Sheets ERROR spam when no credentials | downgrade to DEBUG | fbf632b |

---

## REFERENCE FILES (load on demand)

- `architecture.md` — module tree, BotState fields, Intent Pipeline gates, trade schema
- `fixes.md` — all 13 fixes with code snippets
- `improvements.md` — 8 planned upgrades with implementation code
- `telegram.md` — Railway setup, relay SQL, webhook config, deploy commands
- `prompt_patterns.md` — bug/feature/audit prompt templates, session checklist
