"""Hybrid AI brain for OpenClaw / ClawBot.

Middleware pipeline (applied on every ask_hybrid call):
  1. Input Optimizer    — normalize & cap raw prompt
  2. Intent Classifier  — 3-tier: chat | cheap_reasoning | precision
  3. Cache check        — 1-hour TTL, MD5-keyed
  4. LLM dispatch       — T1/T2 → Ollama, T3 → Claude Haiku (fallback: Ollama)
  5. Output Compressor  — strip filler openers, collapse blank lines
  6. Memory Router      — append insights to memory/ JSONL stores
  7. Cache write        — store result for future hits

Tiers:
  T1 chat            < 8 words, casual            → Ollama
  T2 cheap_reasoning analytical, < 50 words       → Ollama
  T3 precision       high-stakes / architectural   → Claude Haiku
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import anthropic
from ollama import chat as ollama_chat

from lib.input_optimizer import optimize_input
from lib.intent_classifier import classify_intent, tier_to_complexity
from lib.output_compressor import compress_output
from lib.memory_router import route_memory, write_memory, log_tier_usage, log_soft_failure

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"
CLAUDE_MODEL         = "claude-haiku-4-5-20251001"
MAX_TOKENS           = int(os.getenv("MAX_TOKENS_PER_RESPONSE", "500"))
COMPLEXITY_THRESHOLD = int(os.getenv("COMPLEXITY_THRESHOLD", "50"))
CACHE_TTL_SECONDS    = 3600

_ROOT       = Path(__file__).parent.parent
_DATA_DIR   = _ROOT / "data"
_CACHE_FILE = _DATA_DIR / "response_cache.json"
_USAGE_FILE = _DATA_DIR / "usage_stats.json"
_PROMPT_FILE = Path(__file__).parent / "system-prompt.md"

# Load unified system prompt from file; fall back to inline default
def _load_system_prompt() -> str:
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return (
            "You are ClawBot — a sharp, decisive AI assistant for Ronnie (OpenClaw brand). "
            "Be direct, concise, and action-oriented. No waffle."
        )

CLAWBOT_SYSTEM: str = _load_system_prompt()

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key(prompt: str) -> str:
    return hashlib.md5(prompt.strip().lower().encode()).hexdigest()


def _get_cached(prompt: str) -> Optional[str]:
    cache = _load_cache()
    key = _cache_key(prompt)
    entry = cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL_SECONDS:
        _track_usage(cache_hit=True)
        return entry["response"]
    return None


def _set_cached(prompt: str, response: str) -> None:
    cache = _load_cache()
    cache[_cache_key(prompt)] = {"response": response, "ts": time.time()}
    if len(cache) > 200:
        oldest = sorted(cache.items(), key=lambda x: x[1]["ts"])
        for k, _ in oldest[:50]:
            del cache[k]
    _save_cache(cache)


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def _track_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "",
    cache_hit: bool = False,
) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats: dict = {}
    if _USAGE_FILE.exists():
        try:
            stats = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    day = stats.setdefault(today, {
        "ollama_calls": 0,
        "claude_calls": 0,
        "claude_input_tokens": 0,
        "claude_output_tokens": 0,
        "cache_hits": 0,
    })

    if cache_hit:
        day["cache_hits"] += 1
    elif "haiku" in model or "claude" in model:
        day["claude_calls"] += 1
        day["claude_input_tokens"] += input_tokens
        day["claude_output_tokens"] += output_tokens
    else:
        day["ollama_calls"] += 1

    _USAGE_FILE.write_text(json.dumps(stats, indent=2), encoding="utf-8")


def get_usage_today() -> dict:
    if not _USAGE_FILE.exists():
        return {}
    try:
        stats = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return stats.get(today, {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Complexity classifier (legacy — kept for backwards compat)
# ---------------------------------------------------------------------------

_COMPLEX_KEYWORDS = {
    "plan", "analyse", "analyze", "strategy", "research", "breakdown",
    "compare", "comparison", "explain", "detailed", "detail", "full",
    "step by step", "pros and cons", "pros cons", "overview", "summary",
    "investigate", "deep dive", "report", "forecast", "prediction",
    "recommendation", "suggest", "evaluate", "assessment",
}


def classify_complexity(prompt: str) -> str:
    """Return 'simple' or 'complex'. Delegates to 3-tier classifier internally."""
    if not os.getenv("USE_CLAUDE_API", "true").lower() == "true":
        return "simple"
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return "simple"
    return tier_to_complexity(classify_intent(prompt))


# ---------------------------------------------------------------------------
# Prompt compression (input side)
# ---------------------------------------------------------------------------

_FILLER = re.compile(
    r"\b(please|kindly|could you|would you|i would like you to|"
    r"can you|just|simply|basically|literally|actually|very|really|"
    r"i want you to|make sure to|feel free to)\b",
    re.IGNORECASE,
)


def _compress(prompt: str) -> str:
    compressed = _FILLER.sub("", prompt)
    compressed = re.sub(r" {2,}", " ", compressed).strip()
    return compressed if compressed else prompt


def _compress_history(history: List[dict], max_turns: int = 6) -> List[dict]:
    return history[-max_turns:] if len(history) > max_turns else history


# ---------------------------------------------------------------------------
# Ollama (T1 + T2 / fallback)
# ---------------------------------------------------------------------------

def ask_llm(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    sys_prompt = system or CLAWBOT_SYSTEM
    messages: List[dict] = [{"role": "system", "content": sys_prompt}]
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": prompt})

    try:
        response = ollama_chat(model=model, messages=messages)
        result = response.message.content.strip()
        _track_usage(model=model)
        return result
    except Exception as exc:
        raise RuntimeError(f"Ollama generation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Claude Haiku (T3 precision tasks)
# ---------------------------------------------------------------------------

def ask_claude(
    prompt: str,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return ask_llm(prompt, system=system, history=history)

    client = anthropic.Anthropic(api_key=api_key)
    messages: List[dict] = []
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": _compress(prompt)})

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system or CLAWBOT_SYSTEM,
            messages=messages,
        )
        result = next(
            (b.text for b in response.content if b.type == "text"), ""
        ).strip()
        _track_usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=CLAUDE_MODEL,
        )
        return result
    except anthropic.AuthenticationError:
        return ask_llm(prompt, system=system, history=history)
    except Exception as exc:
        try:
            return ask_llm(prompt, system=system, history=history)
        except Exception:
            raise RuntimeError(f"Both Claude and Ollama failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Hybrid router — full middleware pipeline
# ---------------------------------------------------------------------------

def ask_hybrid(
    prompt: str,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
    force: Optional[str] = None,
) -> tuple[str, str]:
    """Route prompt through the full middleware pipeline.

    Pipeline:
        optimize → classify → cache? → dispatch → compress → memory → cache write

    Args:
        prompt: Raw user text.
        system: Override system prompt.
        history: Conversation history.
        force: "simple" | "complex" bypasses the classifier.

    Returns:
        (response_text, brain_used)  brain_used ∈ {"ollama", "claude", "cache"}
    """
    # 1. Input optimization
    prompt = optimize_input(prompt)

    # 2. Cache hit
    cached = _get_cached(prompt)
    if cached:
        return cached, "cache"

    # 3. Intent classification → tier
    if force == "complex":
        tier = "precision"
    elif force == "simple":
        tier = "chat"
    else:
        use_claude = (
            os.getenv("USE_CLAUDE_API", "true").lower() == "true"
            and bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
        )
        tier = classify_intent(prompt) if use_claude else "chat"

    complexity = tier_to_complexity(tier)

    # 4. Dispatch
    if complexity == "complex":
        raw = ask_claude(prompt, system=system, history=history)
        brain = "claude"
    else:
        raw = ask_llm(prompt, system=system, history=history)
        brain = "ollama"

    # 5. Output compression
    result = compress_output(raw)

    # 6. Self-audit: log inefficiencies
    if len(result) > MAX_TOKENS * 6 and tier != "precision":
        log_soft_failure("output_too_long", tier, len(prompt))
    if complexity == "complex" and len(prompt.split()) < 10:
        log_soft_failure("overkill_tier", tier, len(prompt))

    # 7. Memory routing
    dest = route_memory(result, prompt)
    write_memory(dest, prompt, result, tier)
    log_tier_usage(tier, len(prompt))

    # 8. Cache write
    _set_cached(prompt, result)
    return result, brain
