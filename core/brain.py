"""Hybrid AI brain for OpenClaw / ClawBot.

Routes requests automatically between:
  - Local Ollama (free, fast) for simple tasks
  - Claude Haiku API (smart) for complex tasks
  - Falls back to Ollama if Claude API is unavailable or key is missing

Complexity detection:
  SIMPLE  — short prompts, casual chat, captions, quick questions
  COMPLEX — keywords: plan, analyse, strategy, research, breakdown,
             compare, explain, detailed, full, step by step

Extras:
  - Response cache (data/response_cache.json) — 1-hour TTL
  - Prompt compression — strips filler, caps Claude responses at MAX_TOKENS
  - Usage tracking (data/usage_stats.json) — token counts per session
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"
CLAUDE_MODEL         = "claude-haiku-4-5"   # user-specified: Haiku for complex tasks
MAX_TOKENS           = int(os.getenv("MAX_TOKENS_PER_RESPONSE", "500"))
COMPLEXITY_THRESHOLD = int(os.getenv("COMPLEXITY_THRESHOLD", "50"))   # word count
CACHE_TTL_SECONDS    = 3600   # 1 hour

_DATA_DIR   = Path(__file__).parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "response_cache.json"
_USAGE_FILE = _DATA_DIR / "usage_stats.json"

_COMPLEX_KEYWORDS = {
    "plan", "analyse", "analyze", "strategy", "research", "breakdown",
    "compare", "comparison", "explain", "detailed", "detail", "full",
    "step by step", "pros and cons", "pros cons", "overview", "summary",
    "investigate", "deep dive", "report", "forecast", "prediction",
    "recommendation", "suggest", "evaluate", "assessment",
}

# ---------------------------------------------------------------------------
# Ollama model resolution — auto-detect with lazy TTL cache
# ---------------------------------------------------------------------------

class OllamaOfflineError(RuntimeError):
    """Raised when Ollama is unreachable (connection refused, not running)."""

MODEL_CACHE_TTL = 60  # seconds before re-checking installed models

_resolved_model: Optional[str] = None
_resolved_model_ts: float = 0.0


def _resolve_ollama_model() -> str:
    """Return the best available Ollama model, with 60-second TTL cache.

    Resolution order:
      1. OLLAMA_MODEL env var, if that model is installed.
      2. First model returned by ollama.list().
      3. OllamaOfflineError if ollama.list() raises (Ollama not running).
    """
    global _resolved_model, _resolved_model_ts
    now = time.time()
    if _resolved_model is not None and (now - _resolved_model_ts) < MODEL_CACHE_TTL:
        return _resolved_model

    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
    except Exception as exc:
        raise OllamaOfflineError(f"Ollama unreachable: {exc}") from exc

    if not models:
        raise OllamaOfflineError("Ollama is running but has no models installed.")

    configured = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    chosen = configured if configured in models else models[0]

    _resolved_model = chosen
    _resolved_model_ts = now
    return chosen


CLAWBOT_SYSTEM = """\
You are ClawBot — a sharp, decisive AI assistant for Ronnie (OpenClaw brand).

You help with:
- Business planning and side hustles
- Crypto trading analysis and DCA strategy
- Content creation for the OpenClaw brand
- Market research and deep dives
- Daily tasks and productivity

Your personality:
- Direct and concise — no waffle, no fluff
- Like a smart business partner, not a yes-man
- Flag risks clearly and push back on bad ideas
- Confident and action-oriented
- Use OpenClaw motivational tone

Rules:
- Always give actionable advice
- Always confirm before executing any task
- Format responses for Telegram: short paragraphs, bullet points
- Max 3-4 sentences per point
- Think like a business partner, not a chatbot
"""


# ---------------------------------------------------------------------------
# Cache helpers
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
    # Keep cache small — drop oldest entries beyond 200
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
    """Return today's usage stats dict."""
    if not _USAGE_FILE.exists():
        return {}
    try:
        stats = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return stats.get(today, {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Complexity classifier
# ---------------------------------------------------------------------------

def classify_complexity(prompt: str) -> str:
    """Return 'simple' or 'complex' based on prompt content."""
    if not os.getenv("USE_CLAUDE_API", "true").lower() == "true":
        return "simple"
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return "simple"

    words = prompt.lower().split()
    # Long prompts are complex
    if len(words) >= COMPLEXITY_THRESHOLD:
        return "complex"
    # Keyword match
    text = prompt.lower()
    for kw in _COMPLEX_KEYWORDS:
        if kw in text:
            return "complex"
    return "simple"


# ---------------------------------------------------------------------------
# Prompt compression
# ---------------------------------------------------------------------------

_FILLER = re.compile(
    r"\b(please|kindly|could you|would you|i would like you to|"
    r"can you|just|simply|basically|literally|actually|very|really|"
    r"i want you to|make sure to|feel free to)\b",
    re.IGNORECASE,
)


def _compress(prompt: str) -> str:
    """Strip filler words and collapse whitespace."""
    compressed = _FILLER.sub("", prompt)
    compressed = re.sub(r" {2,}", " ", compressed).strip()
    return compressed if compressed else prompt


def _compress_history(history: List[dict], max_turns: int = 6) -> List[dict]:
    """Keep only the last max_turns messages to limit token usage."""
    return history[-max_turns:] if len(history) > max_turns else history


# ---------------------------------------------------------------------------
# Ollama (simple tasks / fallback)
# ---------------------------------------------------------------------------

def ask_llm(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """Ask local Ollama. Uses auto-detected model unless overridden."""
    resolved = model or _resolve_ollama_model()  # may raise OllamaOfflineError
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": prompt})

    try:
        response = ollama_chat(model=resolved, messages=messages)
        result = response.message.content.strip()
        _track_usage(model=resolved)
        return result
    except Exception as exc:
        raise RuntimeError(f"Ollama generation failed ({resolved}): {exc}") from exc


# ---------------------------------------------------------------------------
# Claude Haiku (complex tasks)
# ---------------------------------------------------------------------------

def ask_claude(
    prompt: str,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """Ask Claude Haiku for complex tasks. Falls back to Ollama on error."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return ask_llm(prompt, system=system, history=history)

    client = anthropic.Anthropic(api_key=api_key)
    messages = []
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
        # Fallback to Ollama on any Claude API error
        try:
            return ask_llm(prompt, system=system, history=history)
        except Exception:
            raise RuntimeError(f"Both Claude and Ollama failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Hybrid router (main public interface)
# ---------------------------------------------------------------------------

def ask_hybrid(
    prompt: str,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
    force: Optional[str] = None,   # "simple" | "complex" | None
) -> tuple[str, str]:
    """Route prompt to Ollama or Claude based on complexity.

    Returns:
        (response_text, brain_used)  where brain_used is "ollama" or "claude"
    """
    # Check cache first
    cached = _get_cached(prompt)
    if cached:
        return cached, "cache"

    complexity = force or classify_complexity(prompt)

    if complexity == "complex":
        result = ask_claude(prompt, system=system, history=history)
        brain = "claude"
    else:
        try:
            result = ask_llm(prompt, system=system, history=history)
            brain = "ollama"
        except OllamaOfflineError:
            # Ollama offline — route to Claude API (logged as warning)
            import logging as _log
            _log.warning("Ollama offline or model unavailable — routing to Claude API")
            result = ask_claude(prompt, system=system, history=history)
            brain = "claude"

    _set_cached(prompt, result)
    return result, brain
