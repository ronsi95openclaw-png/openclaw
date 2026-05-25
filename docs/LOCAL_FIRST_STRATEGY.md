# OpenClaw — Local-First Strategy

**Last Updated**: 2026-05-25

---

## Philosophy

OpenClaw is designed as a local-first system. The local machine (Ronnie's machine, IP 166.198.250.23) is the primary execution environment. Railway is a 24/7 monitoring and paper-trade simulation layer, not a live trading environment. Cloud is used ONLY when it provides features the local machine cannot (24/7 uptime, remote dashboard access).

This architecture minimizes API costs, protects trading strategy privacy (Ollama runs entirely local), and avoids cloud vendor lock-in for critical path operations.

---

## What Runs Local vs Cloud

### Local Machine (Primary)

| Component | Technology | Cost |
|-----------|-----------|------|
| Trading bot scan loop | Python, 30-60s interval | $0 |
| QUIN gate | qwen2.5:14b via Ollama | $0 |
| Per-trade compression | qwen3 via Ollama | $0 |
| Market data | Crypto.com REST API | $0 |
| Ruflo HNSW memory | Node.js subprocess | $0 |
| Telegram bot | Polling, real-time | $0 |
| Google Sheets reporting | REST API | $0 |
| Nightly Opus analysis | Claude Opus 4.7 API | ~$0.30/day |
| Complex Telegram queries | Claude Haiku 4.5 API | ~$0.05/day |
| Obsidian vault writes | Local filesystem | $0 |
| Dashboard API | FastAPI :8000 | $0 |
| Dashboard web | Next.js :3000 | $0 |

**Total local cost**: ~$0.35/day ($10.50/month)

### Railway (Cloud — 24/7 Uptime)

| Component | Technology | Cost |
|-----------|-----------|------|
| Bot simulation (fake candles) | Python main.py | $0 compute |
| Dashboard API | FastAPI :8000 | Free tier |
| QUIN rule-based fallback | Deterministic (no LLM) | $0 |
| OpenRouter LLM fallback | OpenRouter API | ~$0.01-0.05/day |

**Railway constraints**:
- api.crypto.com: **BLOCKED** — no real trading possible
- api.telegram.org: **BLOCKED** — no Telegram in cloud
- Ollama: **NOT AVAILABLE** — no GPU, no local inference
- Ruflo Node.js: **NOT AVAILABLE** — no subprocess in Docker
- Obsidian vault: **BROKEN** — ~/ai-system path missing

**Total Railway cost**: Free tier + ~$0.05/day OpenRouter = ~$1.50/month

---

## Network Topology

```
Local Machine (IP: 166.198.250.23)
├── Ollama (localhost:11434) — qwen2.5:14b, qwen3, deepseek-coder, gemma3
├── Ruflo MCP (stdio subprocess) — Node.js, HNSW memory
├── Crypto.com API (whitelisted) → api.crypto.com
├── Telegram API → api.telegram.org
├── Google Sheets API → sheets.googleapis.com
├── Anthropic API → api.anthropic.com (Opus + Haiku)
└── Obsidian Vault (~/AI-Operating-System-Vault/) — local filesystem

Railway Cloud
├── FastAPI server (PORT env var, default 8000)
├── CryptoComBot (fake candles simulation)
├── OpenRouter → openrouter.ai (cloud LLM fallback)
└── ❌ Cannot reach: api.crypto.com, api.telegram.org, Ollama, Ruflo
```

---

## Fallback Chain Design

Every LLM call has a fallback chain:

```python
# core/brain.py — ask_llm() fallback chain:

for candidate in fallback_chain(task):   # e.g. ["qwen3", "qwen2.5:14b"]
    try:
        return ollama_chat(model=candidate, messages=messages)
    except Exception:
        continue

# Ollama completely unavailable (Railway):
if OPENROUTER_API_KEY:
    return _ask_openrouter(prompt, chain[0])   # translate to OpenRouter model

raise RuntimeError("All inference sources failed")
```

```python
# runtime/quin_orchestrator.py — QUIN fallback:
try:
    result = call_ollama(model="qwen2.5:14b", timeout=10s)
    return parse_decision(result)
except (TimeoutError, ConnectionError):
    return _rule_based_decide(ctx)   # deterministic, always works
```

```python
# runtime/ruflo_agent.py — Ruflo fallback:
if not self._bridge.is_running():
    return RufloAdvice(available=False)  # graceful degradation
```

The trading bot NEVER blocks on an unavailable LLM service. All paths have a deterministic fallback.

---

## Autonomous Local Execution Design

The bot runs autonomously on the local machine without needing any cloud connectivity or human intervention for normal operations:

```
Autonomous loop (60s):
  ├── Scan market data
  ├── Run strategies
  ├── QUIN decision (Ollama or rule-based)
  ├── IntentPipeline gates
  ├── Execute (DEMO_MODE=true → simulation)
  ├── Record outcomes
  ├── Obsidian vault writes
  └── Telegram alerts (if bot not halted)

Midnight (UTC):
  ├── Claude Opus nightly analysis
  └── Weight application daemon

Human touchpoints:
  ├── /status /trades /goal /balance (Telegram commands)
  ├── Dashboard web (http://localhost:3000)
  └── Emergency /restart command
```

---

## Caching Strategy

### Response cache (core/brain.py)

```python
CACHE_TTL_SECONDS = 3600    # 1 hour
MAX_CACHE_ENTRIES = 200     # evict oldest 50 when exceeded
cache_key = MD5(prompt.strip().lower())
```

**Cached**: `ask_hybrid()` responses (Telegram queries, general brain calls)
**Not cached**: QUIN decisions (always fresh), Opus analysis (direct API call), JSONL writes

**Cache hit savings**: Telegram `/status` queries are often identical. Cache prevents ~10-20 Claude Haiku calls/day = ~$0.02-0.04/day saved.

### MCP cache (data/mcp_cache/)

Crypto.com MCP market data is cached locally to reduce API calls during backtesting.

### Data response cache

Strategy signals and weights are held in memory in `StrategyWeightEngine`. No disk read on every scan — weights are loaded at startup and updated in memory, persisted to disk on change.

---

## Token Reduction Techniques

### 1. Prompt compression (core/brain.py _compress())

Strips filler words from user prompts before sending to Claude:
- Estimated savings: 5-15% of prompt tokens on conversational inputs
- Applied to: all `ask_claude()` calls

### 2. History trimming

```python
_compress_history(history, max_turns=6)  # keeps last 6 messages only
```

Without this, a long Telegram conversation would grow unbounded token usage.

### 3. Trade outcomes truncation (claude_analyst.py)

Opus analysis reads the last N trade outcomes, not the full history. Default: 100 trades. At 2-3 trades/day, this covers 1-2 months of history — sufficient for pattern detection.

### 4. MAX_TOKENS cap

```python
MAX_TOKENS = int(os.getenv("MAX_TOKENS_PER_RESPONSE", "500"))
```

All Claude Haiku calls are capped at 500 tokens. Only Opus analysis uses 4096.

### 5. QUIN timeout (10 seconds)

```python
_QUIN_TIMEOUT_S = 10.0   # max wait for Ollama response
```

Prevents slow Ollama responses from blocking the scan loop. Falls back to rule-based immediately on timeout.

---

## OpenRouter as Cloud Bridge

OpenRouter provides a single API key that routes to 200+ models. This solves the Railway problem of having no local inference available.

### Configuration

```bash
# Railway environment variables:
OPENROUTER_API_KEY=your-key-here
OPENROUTER_SITE_URL=https://openclaw.app
OPENROUTER_SITE_NAME=OpenClaw
```

### How it's used

```python
# core/brain.py — _ask_openrouter():
or_model = OPENROUTER_MODEL_MAP.get(model_name, "qwen/qwen-2.5-14b-instruct")
# Sends OpenAI-compatible request to https://openrouter.ai/api/v1/chat/completions
# 20-second timeout
```

### Cost control

OpenRouter charges per token. Current Railway usage is minimal because:
1. QUIN uses rule-based fallback (no OpenRouter calls for trading decisions)
2. Telegram is blocked (no user queries)
3. Only internal ask_llm() calls for any non-Ollama LLM needs

Estimated: 0-50 OpenRouter calls/day on Railway = ~$0.001-0.01/day.

---

## Cost Matrix by Task Type

| Task | Local cost | Cloud (Railway) cost | Notes |
|------|-----------|---------------------|-------|
| Per-scan QUIN decision | $0 (Ollama) | $0 (rule-based) | 1440 scans/day |
| Strategy signal generation | $0 (Python) | $0 (Python) | Deterministic |
| Market data fetch | $0 (API) | $0 (fake candles) | Real data local only |
| Per-trade compression | $0 (qwen3) | ~$0.0002 (OpenRouter) | ~10 trades/day |
| Telegram command response | ~$0.002 (Haiku) | N/A (blocked) | ~20 queries/day |
| Nightly Opus analysis | ~$0.05-0.30 (Opus) | ~$0.05-0.30 (Opus) | 1/day |
| Weight application | $0 (Python) | $0 (Python) | 1/day |
| Obsidian vault writes | $0 (filesystem) | $0 (filesystem) | ~15/day |
| Ruflo HNSW memory | $0 (local) | N/A (unavailable) | ~10 lookups/day |

**Key insight**: The expensive operations (Opus analysis) are identical cost locally and in cloud. The cheap-but-frequent operations (QUIN gate) are free locally and free (rule-based) in cloud. The system is already cost-optimized for its traffic pattern.

---

## Optimization Opportunities

### 1. Shift Telegram queries to Ollama (save ~$0.04/day)

```python
# Current: classify_complexity returns "complex" for many Telegram queries
# Optimization: add domain-specific classifier for bot commands

def is_bot_command_query(prompt: str) -> bool:
    bot_commands = ["/status", "/balance", "/trades", "/goal", "/weights"]
    return any(cmd in prompt.lower() for cmd in bot_commands)

# In ask_hybrid():
if is_bot_command_query(prompt):
    return ask_llm(prompt, task="structured")  # Ollama qwen3
```

### 2. Cache QUIN decisions for duplicate signals (save Ollama compute)

```python
# QUIN sees the same signals repeatedly in low-activity periods
# Cache key: (strategy, symbol, action, regime, confidence_bucket)
# TTL: 30 seconds (one scan interval)
```

### 3. Batch Obsidian writes

Current: one disk write per trade close, per weight change
Optimization: buffer writes and flush every 5 minutes

```python
class BatchedObsidianWriter:
    def __init__(self, flush_interval=300):
        self._buffer = []
        self._flush_thread = threading.Thread(target=self._flush_loop)
```

### 4. Compress Opus prompt (save ~$0.02/day)

The nightly Opus analysis prompt includes raw trade JSON. With 100 trades, this can be 15-20k tokens. Summarizing by strategy first reduces by ~60%:

```python
# Instead of raw JSONL:
outcomes_json = json.dumps(trade_records)  # ~15k chars

# Use pre-aggregated summary:
outcomes_summary = {
    "EMA_CROSS": {
        "total": 25, "wins": 13, "avg_pnl": 12.40,
        "by_regime": {"TRENDING_BULL": {"w": 5, "l": 3}, ...}
    }
}
# + last 10 most recent raw records for detail
```

---

## Local vs Cloud Decision Flowchart

```
Is DEMO_MODE=false required?
  YES → Must run on LOCAL (Crypto.com API whitelisted for 166.198.250.23)
  NO  → Can run anywhere

Does the task require Telegram?
  YES → Must run on LOCAL (api.telegram.org blocked in Railway)
  NO  → Can run anywhere

Does the task require Ruflo HNSW?
  YES → Must run on LOCAL (Node.js subprocess unavailable in Railway)
  NO  → Can run anywhere

Does the task require real Ollama (low-latency, free)?
  YES → Prefer LOCAL (no GPU in Railway, OpenRouter adds cost + latency)
  NO  → Railway OK

Does the task require 24/7 uptime independent of local machine?
  YES → Deploy to RAILWAY
  NO  → Local is sufficient
```

**Conclusion**: For live trading ($50K goal), the bot MUST run on the local machine. Railway is a preview/monitoring layer only.
