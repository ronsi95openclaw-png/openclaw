# OpenClaw — Claude Code Context

## What This Is

OpenClaw is a personal crypto trading + content pipeline bot for Ronnie. It runs as a
Telegram bot (ClawBot) with a hybrid AI brain (Ollama locally, Claude Haiku for complex
tasks), an RSI+MACD trading engine on Crypto.com, and a video-editing pipeline for
TikTok/Instagram reels.

## Entry Points

- **Bot**: `python -m content.receiver` — Telegram bot, async (python-telegram-bot)
- **Dashboard**: `python dashboard/app.py` — Flask, read-only, port 8080
- **Pipeline (standalone)**: `python -m content.pipeline --once <video>`

## Architecture

```
Telegram → receiver.py
  → security/whitelist.py     (allowlist check — ALLOWED_CHAT_ID env var)
  → core/brain.py             (Ollama default, Claude Haiku for complex)
  → core/conversation.py      (10-turn history per chat_id, JSON file)
  → core/scheduler.py         (APScheduler: reminders + daily auto-trade 08:00 UTC)
  → trading/strategy.py       (RSI+MACD signals — BTC/SOL/XRP/ETH)
  → trading/executor.py       (Crypto.com orders, HIGH confidence only, 1.5% risk)
  → content/pipeline.py       (video → editor → captions → Telegram approval → post)
```

## Key Rules

- **Security**: `ALLOWED_CHAT_ID` must be set. Empty = ALL messages denied.
- **Trading**: Only `HIGH` confidence signals are executed. 1.5% portfolio risk per trade.
- **AI routing**: prompts < 50 words → Ollama (free). Complex keywords or > 50 words → Claude Haiku.
- **Cache**: responses cached 1 hour in `data/response_cache.json` (max 200 entries).
- **async/sync**: `send_status_sync` and `send_for_approval_sync` use `requests` directly —
  do NOT convert them back to `asyncio.run()` (breaks inside the Telegram event loop).

## Data Files (runtime, gitignored)

| File | Written by |
|------|-----------|
| `data/response_cache.json` | `core/brain.py` |
| `data/usage_stats.json` | `core/brain.py` |
| `data/tasks.json` | `core/scheduler.py` |
| `data/autotrade.json` | `core/scheduler.py` |
| `data/conversation_history.json` | `core/conversation.py` |
| `data/logs/trades.log` | `trading/executor.py` |

`core/startup.ensure_data_dirs()` creates `data/` and `data/logs/` at startup.

## Middleware Pipeline (runs on every `ask_hybrid` call)

```
optimize_input()       lib/input_optimizer.py   — collapse whitespace, cap 2000 chars
classify_intent()      lib/intent_classifier.py — T1 chat | T2 cheap_reasoning | T3 precision
cache check            core/brain.py            — 1-hour TTL, MD5 keyed
LLM dispatch           core/brain.py            — T1/T2 → Ollama, T3 → Claude Haiku
compress_output()      lib/output_compressor.py — strip filler openers, collapse blank lines
self-audit             core/brain.py            — log overkill tier / overlong output
route_memory()         lib/memory_router.py     — long_term | working | discard
log_tier_usage()       lib/memory_router.py     — appends to memory/tier-usage.jsonl
cache write            core/brain.py            — store for future hits
```

## Memory Files (runtime, gitignored JSONL)

| File | Written by | Contains |
|------|-----------|----------|
| `memory/long_term.jsonl` | `lib/memory_router.py` | Durable insights, decisions, strategies |
| `memory/working.jsonl` | `lib/memory_router.py` | Session-relevant context |
| `memory/tier-usage.jsonl` | `lib/memory_router.py` | Per-request tier + token stats |
| `memory/soft-failures.jsonl` | `core/brain.py` | Misroutes and over-long outputs |

## Three-Tier Routing

| Tier | Trigger | Model |
|------|---------|-------|
| T1 chat | < 8 words, no keywords | Ollama |
| T2 cheap_reasoning | analytical keywords, or ≥ 50 words | Ollama |
| T3 precision | design / architect / strategy / evaluate / audit | Claude Haiku |

## Models

- Ollama: `qwen2.5:14b` (default, set via `OLLAMA_MODEL`)
- Claude: `claude-haiku-4-5-20251001` (set in `core/brain.py:CLAUDE_MODEL`)

## Known Limitations

- `content/poster.py` Instagram flow requires a publicly accessible video URL for the
  container creation step — local file upload to Instagram Graph API is not supported
  without a separate hosting step.
- Conversation history JSON file has no write lock — acceptable for single-user bot
  but would need `fcntl`/`filelock` for multi-user deployments.
- Whisper transcription in `content/editor.py` runs synchronously in the Telegram
  handler thread — long videos will block the handler during processing.

## Common Tasks

**Add a new Telegram command**: Add a handler function + register with `CommandHandler`
in `receiver.py` near the other `app.add_handler(...)` calls.

**Add a new trading coin**: Update `RSIMACDConfig.coins` in `trading/strategy.py` and
add a minimum order size entry in `trading/executor._MIN_ORDER_USD`.

**Change AI model**: Update `CLAUDE_MODEL` in `core/brain.py`. Use full dated model IDs.

**Change complexity threshold**: Set `COMPLEXITY_THRESHOLD` env var (word count above
which prompts go to Claude). Default: 50.
