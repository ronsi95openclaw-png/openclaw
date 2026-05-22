# OpenClaw — Session Compact (2026-05-22)

This document is the authoritative knowledge dump for the OpenClaw project.
It is written for both Claude (next session) and Ronnie (via VSCode / Obsidian).
Update it after each major session.

---

## Project snapshot

| Item | Value |
|---|---|
| Mode | DEMO_MODE=true (paper trading only) |
| Exchange | Crypto.com perpetual futures |
| Pairs | BTC_USDT · ETH_USDT · SOL_USDT |
| Leverage | 3× |
| Branch | `claude/blofin-trading-bot-dashboard-TUJBC` |
| Capability matrix | 31/31 OK |

---

## Architecture (top → bottom)

```
Claude Opus 4.6          runtime/claude_analyst.py   — nightly strategy review
Ruflo HNSW               runtime/ruflo_agent.py      — pre-trade memory advisory
qwen3 / qwen2.5:14b      core/brain.py               — compression + reasoning
deepseek-coder           core/brain.py               — code tasks
gemma3                   core/brain.py               — utility tasks
Intent Pipeline          runtime/intent_pipeline.py  — 5-gate validation
Capital Engine           risk/capital_preservation.py— SAFE/DEFENSIVE/CRITICAL/HALT
CryptoComBot             trading/cryptocom_bot.py    — 30s scan loop
Executor                 trading/executor.py         — live order placement
Crypto.com API           trading/exchange.py         — REST + MCP market data
Google Sheets            reporting/google_sheets.py  — 5 tabs
Dashboard API            dashboard/api/server.py     — FastAPI + WebSocket :8000
React UI                 dashboard/web/              — Next.js :3000
Obsidian Vault           ~/AI-Operating-System-Vault/— long-term memory
AI-System lib            ~/ai-system/                — vault writers, model router
```

---

## MODEL_REGISTRY (core/brain.py)

| Task key | Primary model | Fallback |
|---|---|---|
| compression | qwen3 | qwen2.5:14b |
| reasoning | qwen3 | qwen2.5:14b |
| code | deepseek-coder | qwen2.5:14b |
| utility | gemma3 | qwen3 → qwen2.5:14b |
| structured | qwen3 | qwen2.5:14b |
| default | qwen2.5:14b | — |

---

## Strategies

| Strategy | Description | Regime |
|---|---|---|
| BOLLINGER_BAND | BB squeeze breakout, %B + RSI filter | Any |
| BREAKOUT | Volume-confirmed breakout above/below range | TRENDING |
| TREND_FOLLOW | EMA20/50 trend + ADX filter | TRENDING |
| EMA_CROSS | EMA9/21 cross | TRENDING |
| RSI_MEAN_REVERT | RSI oversold/overbought mean reversion | RANGING |

Weights: self-learning via recency-weighted win rate (0.85^i decay, last 20 outcomes).
Range: [0.2, 2.0]. Strategies auto-suspended at weight < 0.3 with 20+ trades.

---

## Position lifecycle

1. Signal generated → IntentPipeline (schema → staleness → dedup → regime → capital)
2. `_open_position()`: 60% initial size, 40% reserved for DCA
3. DCA add-on: triggers at 50% of SL distance, averages entry
4. Partial TP: at 50% of TP distance → close 50% of size, move SL to breakeven
5. Full close at SL or TP → record outcome → update strategy weight → Obsidian vault

---

## Bug fixes committed this session

| ID | File | Description |
|---|---|---|
| BUG-008 | trading/cryptocom_bot.py | BOLLINGER_BAND never called (missing from `_scan()`) |
| BUG-009 | trading/cryptocom_bot.py | Correlated exposure gate blocked ALL first positions |
| FIX-1 | trading/strategies.py | EMA_CROSS corrupt state reset (weight was staying 0.0) |
| FIX-2 | trading/cryptocom_bot.py | Confidence stored as int 0-100 → migrated to 0-1 float |
| FIX-3 | trading/cryptocom_bot.py | scan_interval not persisted across restarts |
| FIX-4 | trading/cryptocom_bot.py | BOLLINGER_BAND squeeze threshold raised 1.5% → 4.0% |
| FIX-5 | trading/cryptocom_bot.py | RSI_MEAN_REVERT spread threshold raised 0.30% → 0.50% |
| FIX-6 | trading/cryptocom_bot.py | DCA default bug: `dca_count` defaulted to 1 instead of 0 |
| FIX-7 | trading/cryptocom_bot.py | Partial TP position mutations outside `_lock` (thread safety) |
| FIX-8 | trading/cryptocom_bot.py | `original_entry` backfill migration on state load |
| FIX-9 | trading/cryptocom_bot.py | Position schema validation on load (drop malformed rows) |
| FIX-10 | trading/cryptocom_bot.py | `_refresh_balance` writes `state.balance` without lock |

---

## Global AI OS rules (all projects)

1. **AI hierarchy**: Claude Opus → Ruflo → Local LLM Pool → Validation → Human
2. **Obsidian vault** at `~/AI-Operating-System-Vault/` is long-term memory. Write after every trade, strategy change, architecture decision, daily summary.
3. **MODEL_REGISTRY** routes tasks to the right model. Never hard-code model names in feature code.
4. **Validation pipeline** (mandatory before merge): lint → type → compile → tests → replay → security
5. **Per-project `.ai/`** directory must exist: architecture/ replay/ validation/ governance/ prompts/ memory/ metrics/ docs/
6. **Never commit** `.env`, `credentials.json`, `setup.sh`.
7. **Never push to main**. Always develop on the designated feature branch.
8. **Always push** after completing a task.

---

## Obsidian vault auto-writers (~/ai-system/obsidian/)

| Module | When called | Target section |
|---|---|---|
| `trade_journal_writer.py` | On position close | `05_Trading/` |
| `strategy_writer.py` | On weight update | `06_Strategies/` |
| `optimization_writer.py` | Daily flush + Opus analysis | `07_Optimization/` |
| `vault_manager.py` | Daily flush | `20_Daily_Notes/` |
| `architecture_writer.py` | Architecture decisions | `01_Architecture/` |
| `replay_writer.py` | Replay traces | `09_Replay/` |
| `ai_memory_writer.py` | Reasoning summaries | `13_Memory/` |

---

## How to run

```bash
# Local machine (not remote container)
cd ~/openclaw

# Start everything
./start.sh

# Or manually:
python dashboard/api/server.py &    # :8000
cd dashboard/web && npm run dev &   # :3000

# Capability matrix
python -m runtime.capability_matrix

# Syntax check
python -m py_compile trading/cryptocom_bot.py
```

---

## Improvement backlog (priority order)

1. Block TREND_FOLLOW in UNKNOWN regime (historically 0% WR)
2. Auto-apply Claude Opus weight_adjustments to strategy_weights.json at midnight
3. Dynamic scan interval — slow in ranging, fast in trending *(partially done: scan_interval saved)*
4. Telegram alerts — wire TELEGRAM_BOT_TOKEN for trade/HALT notifications
5. Auto-disable strategies with weight < 0.3 for 20+ trades *(code present, needs test)*
6. Feed real Crypto.com balance into CapitalPreservationEngine

---

## Key invariants to never break

- `dca_count` defaults to **0** (not 1) — DCA fires when count == 0
- Partial TP mutations and `total_pnl` update must be inside the **same** `_lock` block
- Correlated exposure check: count same-direction existing positions, block only at **≥ 2**
- IntentPipeline is the **only** gate for trade approval — never bypass it
- DEMO_MODE stays **true** until Ronnie explicitly says to go live
