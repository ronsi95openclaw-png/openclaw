# Model Routing Architecture

> Last updated: 2026-05-25

## Current Routing Stack

```
Task arrives
    ↓
classify_complexity(prompt)
    ├── SIMPLE  → ask_llm()  → Ollama → OpenRouter fallback
    └── COMPLEX → ask_claude() → Claude Haiku → Ollama fallback
                                             ↓ (nightly only)
                                        Claude Opus (claude_analyst.py)
```

**Missing tier**: Claude Sonnet. The current stack jumps from Haiku to Opus with no mid-level coordination tier.

---

## MODEL_REGISTRY (core/brain.py)

Task-based routing for Ollama local models:

| Task key | Primary | Fallback chain |
|----------|---------|----------------|
| `compression` | qwen3 | qwen2.5:14b |
| `reasoning` | qwen3 | qwen2.5:14b |
| `code` | deepseek-coder | qwen2.5:14b |
| `utility` | gemma3 | qwen3 → qwen2.5:14b |
| `structured` | qwen3 | qwen2.5:14b |
| `default` | qwen2.5:14b | — |

Override per-task via env var: `OLLAMA_MODEL_COMPRESSION=qwen2.5:14b`

---

## Complexity Classifier

**File**: `core/brain.py` → `classify_complexity(prompt)`

```python
COMPLEXITY_THRESHOLD = 50  # word count (env: COMPLEXITY_THRESHOLD)

_COMPLEX_KEYWORDS = {
    "plan", "analyse", "analyze", "strategy", "research", "breakdown",
    "compare", "comparison", "explain", "detailed", "detail", "full",
    "step by step", "pros and cons", "overview", "summary",
    "investigate", "deep dive", "report", "forecast", "prediction",
    "recommendation", "suggest", "evaluate", "assessment",
}

def classify_complexity(prompt: str) -> str:
    if len(prompt.split()) >= COMPLEXITY_THRESHOLD: return "complex"
    if any(kw in prompt.lower() for kw in _COMPLEX_KEYWORDS): return "complex"
    return "simple"
```

**Limitation**: Binary classifier. No concept of task TYPE (coding vs. strategy vs. chat). Routing is purely word-count + keyword based.

---

## Routing Decision Per Use Case

| Use case | Current model | Correct model | Cost impact |
|----------|---------------|---------------|-------------|
| Per-trade lesson compression | qwen2.5:14b (Ollama) | qwen3 (better structured) | zero |
| QUIN signal gate reasoning | qwen2.5:14b (Ollama) | qwen2.5:14b ✅ | zero |
| Telegram /status /balance | Claude Haiku | Ollama qwen3 | save ~$0.001/call |
| Telegram /goal /weights | Claude Haiku | Ollama qwen3 | save ~$0.001/call |
| Nightly strategy analysis | Claude Opus | Claude Opus ✅ | $0.015–$0.05 |
| Weight adjustment reasoning | none (rule-based) | Claude Sonnet | add tier |
| Risk state narration | none | Ollama qwen3 | zero |
| Regime cross-symbol synthesis | none | Claude Sonnet | mid-cost |
| Code generation / refactor | deepseek-coder (Ollama) | deepseek-coder ✅ | zero |
| Content captions | Claude Haiku | Ollama gemma3 | save cost |

---

## Proposed Production Routing Table

```
Task Complexity → Model Selection

TIER 0: Local inference (free, <100ms)
    Simple ops, compression, formatting, utility scripts
    → Ollama: qwen3 / qwen2.5:14b / gemma3 / deepseek-coder
    → Trigger: word count <50, no complex keywords, no structured output needed

TIER 1: OpenRouter cloud inference (<500ms, low cost)
    Same as Tier 0 but Ollama is unavailable (Railway cloud)
    → OpenRouter: qwen/qwen-2.5-14b-instruct (~$0.0004/1k tokens)
    → Trigger: ollama_chat is None or Ollama connection refused

TIER 2: Claude Haiku (fast, cheap API, ~200ms)
    User-facing queries, simple strategy questions, Telegram responses
    that need natural language quality beyond Ollama
    → anthropic: claude-haiku-4-5 ($0.00025/1k input, $0.00125/1k output)
    → Trigger: USE_CLAUDE_API=true + complexity=simple + user-facing

TIER 3: Claude Sonnet (mid-tier reasoning, ~1-2s)  ← MISSING
    Multi-symbol regime synthesis, weight adjustment reasoning,
    conflict resolution between strategy signals, risk narration
    → anthropic: claude-sonnet-4-6 ($0.003/1k input, $0.015/1k output)
    → Trigger: complexity=complex + NOT nightly_analysis + NOT code

TIER 4: Claude Opus (executive intelligence, ~5-10s)
    Nightly strategy analysis, architecture decisions, governance reviews,
    strategic planning, complex multi-factor reasoning
    → anthropic: claude-opus-4-7 ($0.015/1k input, $0.075/1k output)
    → Trigger: run_analysis=True OR force=complex OR word_count>200
```

---

## Cost Optimization Matrix

| Scenario | Daily calls | Current cost | Optimized cost | Saving |
|----------|-------------|--------------|----------------|--------|
| Telegram commands | ~20/day | Haiku: $0.04 | Ollama: $0 | $0.04 |
| Trade compression | ~10/day | Ollama: $0 | Ollama: $0 ✅ | $0 |
| QUIN gate | ~1440/day | Ollama: $0 | Ollama: $0 ✅ | $0 |
| Nightly analysis | 1/day | Opus: $0.05 | Opus: $0.05 ✅ | $0 |
| Weight reasoning | 0/day | none | Sonnet: $0.02 | new |
| OpenRouter (Railway) | varies | ~$0.01/day | ~$0.005/day | 50% |

**Total current cloud spend**: ~$0.09–$0.15/day (Haiku + Opus + OpenRouter)
**Optimized**: ~$0.05–$0.08/day (shift Telegram to Ollama, add Sonnet for synthesis)

---

## How to Add Claude Sonnet Routing

Add to `core/brain.py`:

```python
SONNET_MODEL = "claude-sonnet-4-6"

def ask_sonnet(
    prompt: str,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """Mid-tier reasoning — regime synthesis, weight analysis, conflict resolution."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return ask_llm(prompt, system=system, history=history)  # Ollama fallback

    client = anthropic.Anthropic(api_key=api_key)
    messages = []
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": _compress(prompt)})

    try:
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=MAX_TOKENS,
            system=system or CLAWBOT_SYSTEM,
            messages=messages,
        )
        result = next((b.text for b in response.content if b.type == "text"), "").strip()
        _track_usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=SONNET_MODEL,
        )
        return result
    except Exception:
        return ask_llm(prompt, system=system, history=history)
```

Update `ask_hybrid()`:

```python
def ask_hybrid(prompt, system=None, history=None, force=None):
    cached = _get_cached(prompt)
    if cached:
        return cached, "cache"

    complexity = force or classify_complexity(prompt)
    word_count  = len(prompt.split())

    if complexity == "complex" and word_count > 150:
        result = ask_claude(prompt, system=system, history=history)   # Opus
        brain  = "opus"
    elif complexity == "complex":
        result = ask_sonnet(prompt, system=system, history=history)   # Sonnet
        brain  = "sonnet"
    else:
        result = ask_llm(prompt, system=system, history=history)      # Ollama
        brain  = "ollama"

    _set_cached(prompt, result)
    return result, brain
```

---

## Response Caching

**File**: `core/brain.py` → `_get_cached()`, `_set_cached()`

- Cache file: `data/response_cache.json`
- TTL: 3600 seconds (1 hour)
- Max entries: 200 (evicts oldest 50 when exceeded)
- Key: MD5 of prompt (case-insensitive, stripped)

**Hit rate**: High for repeated Telegram `/status` queries.  
**Miss rate**: Always misses for timestamped or market-data prompts.

Extend with a per-task TTL:

```python
CACHE_TTL = {
    "market":     300,   # 5min — price data changes fast
    "status":    3600,   # 1hr  — config rarely changes
    "analysis": 86400,   # 24hr — nightly analysis stable
    "default":   3600,
}
```

---

## OpenRouter Model Map

When Ollama is unavailable (cloud/Railway), requests fall through to OpenRouter:

```python
OPENROUTER_MODEL_MAP = {
    "qwen3":          "qwen/qwen3-14b",
    "qwen2.5:14b":    "qwen/qwen-2.5-14b-instruct",
    "deepseek-coder": "deepseek/deepseek-coder",
    "gemma3":         "google/gemma-3-12b-it",
    "qwen2.5:7b":     "qwen/qwen-2.5-7b-instruct",
}
```

Cost on OpenRouter (approximate):
- qwen/qwen-2.5-14b-instruct: ~$0.0004/1k tokens
- qwen/qwen3-14b: ~$0.0008/1k tokens
- deepseek/deepseek-coder: ~$0.0006/1k tokens

These are 10–100× cheaper than Claude for operational tasks.

---

## Environment Variables

| Variable | Default | Effect |
|----------|---------|--------|
| `USE_CLAUDE_API` | `true` | Enables Claude Haiku for complex tasks |
| `ANTHROPIC_API_KEY` | — | Required for any Claude call |
| `OPENROUTER_API_KEY` | — | Required for OpenRouter cloud fallback |
| `OLLAMA_MODEL` | `qwen2.5:14b` | Default Ollama model |
| `QUIN_MODEL` | `qwen2.5:14b` | QUIN gate model |
| `MAX_TOKENS_PER_RESPONSE` | `500` | Claude token cap |
| `COMPLEXITY_THRESHOLD` | `50` | Word count for complex classification |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OPENROUTER_SITE_URL` | `https://openclaw.app` | OpenRouter attribution |
