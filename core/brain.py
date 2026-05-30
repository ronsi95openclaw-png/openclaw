"""Hybrid AI brain for OpenClaw / ClawBot.

Routes requests automatically between:
  - Local Ollama (free, fast) for simple tasks when available
  - OpenRouter API (cheap open-source models) when Ollama is offline
  - Claude Haiku API (smart) for complex tasks
  - Falls back down the chain if any layer is unavailable

Routing priority:
  SIMPLE  → Ollama → OpenRouter → Claude
  COMPLEX → Claude → OpenRouter → Ollama

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

try:
    from ollama import chat as ollama_chat
    _OLLAMA_IMPORTABLE = True
except ImportError:
    _OLLAMA_IMPORTABLE = False

try:
    from openai import OpenAI as _OpenAIClient
    _OPENAI_IMPORTABLE = True
except ImportError:
    _OPENAI_IMPORTABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_MODEL      = "qwen2.5:14b"
DEFAULT_OPENROUTER_MODEL  = "meta-llama/llama-3.1-8b-instruct"
CLAUDE_MODEL              = "claude-haiku-4-5"   # Haiku for complex tasks
MAX_TOKENS                = int(os.getenv("MAX_TOKENS_PER_RESPONSE") or 500)
COMPLEXITY_THRESHOLD      = int(os.getenv("COMPLEXITY_THRESHOLD") or 50)   # word count
CACHE_TTL_SECONDS         = 3600   # 1 hour

_DATA_DIR   = Path(__file__).parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "response_cache.json"
_USAGE_FILE = _DATA_DIR / "usage_stats.json"

_OLLAMA_STATUS: dict = {"ok": None, "ts": 0.0}
_OLLAMA_CACHE_TTL = 60  # recheck every 60 seconds


def _ollama_online() -> bool:
    """Return True if the local Ollama daemon is reachable. Cached for 60s."""
    if not _OLLAMA_IMPORTABLE:
        return False
    now = time.time()
    if now - _OLLAMA_STATUS["ts"] < _OLLAMA_CACHE_TTL and _OLLAMA_STATUS["ok"] is not None:
        return bool(_OLLAMA_STATUS["ok"])
    try:
        from ollama import list as _ol_list
        _ol_list()
        _OLLAMA_STATUS.update({"ok": True, "ts": now})
        return True
    except Exception:
        _OLLAMA_STATUS.update({"ok": False, "ts": now})
        return False


def _openrouter_available() -> bool:
    """Return True if an OpenRouter API key is configured."""
    return _OPENAI_IMPORTABLE and bool(os.getenv("OPENROUTER_API_KEY", "").strip())


_COMPLEX_KEYWORDS = {
    "plan", "analyse", "analyze", "strategy", "research", "breakdown",
    "compare", "comparison", "explain", "detailed", "detail", "full",
    "step by step", "pros and cons", "pros cons", "overview", "summary",
    "investigate", "deep dive", "report", "forecast", "prediction",
    "recommendation", "suggest", "evaluate", "assessment",
}

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
        "openrouter_calls": 0,
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
    elif model == "openrouter":
        day["openrouter_calls"] += 1
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
    has_claude = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    has_any_llm = _ollama_online() or _openrouter_available() or has_claude

    if not has_any_llm:
        return "simple"

    if os.getenv("USE_CLAUDE_API", "true").lower() != "true":
        return "simple"
    if not has_claude:
        return "simple"

    words = prompt.lower().split()
    if len(words) >= COMPLEXITY_THRESHOLD:
        return "complex"
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
    """Ask local Ollama. Falls back to Claude if Ollama is offline."""
    if not _ollama_importable_and_online():
        return ask_claude(prompt, system=system, history=history)

    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": prompt})

    try:
        response = ollama_chat(model=model, messages=messages)
        result = response.message.content.strip()
        _track_usage(model=model)
        return result
    except Exception as exc:
        # Ollama failed mid-request — try Claude
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            return ask_claude(prompt, system=system, history=history)
        raise RuntimeError(f"Ollama generation failed (and no Claude key): {exc}") from exc


def _ollama_importable_and_online() -> bool:
    return _OLLAMA_IMPORTABLE and _ollama_online()


# ---------------------------------------------------------------------------
# OpenRouter (simple tasks on cloud / Ollama fallback)
# ---------------------------------------------------------------------------

def ask_openrouter(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """Ask a model via OpenRouter's OpenAI-compatible API."""
    if not _openrouter_available():
        raise RuntimeError("OpenRouter API key not configured")

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)

    client = _OpenAIClient(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": _compress(prompt)})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=MAX_TOKENS,
            extra_headers={"HTTP-Referer": "https://openclaw.app", "X-Title": "ClawBot"},
        )
        result = response.choices[0].message.content.strip()
        _track_usage(model="openrouter")
        return result
    except Exception as exc:
        raise RuntimeError(f"OpenRouter failed: {exc}") from exc


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
        if _ollama_importable_and_online():
            return ask_llm(prompt, system=system, history=history)
        raise
    except Exception as exc:
        if _ollama_importable_and_online():
            try:
                return ask_llm(prompt, system=system, history=history)
            except Exception:
                pass
        raise RuntimeError(f"Claude API failed: {exc}") from exc


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
        # Complex: Claude → OpenRouter → Ollama
        try:
            result = ask_claude(prompt, system=system, history=history)
            brain = "claude"
        except Exception:
            if _openrouter_available():
                result = ask_openrouter(prompt, system=system, history=history)
                brain = "openrouter"
            elif _ollama_importable_and_online():
                result = ask_llm(prompt, system=system, history=history)
                brain = "ollama"
            else:
                raise
    else:
        # Simple: Ollama → OpenRouter → Claude
        if _ollama_importable_and_online():
            result = ask_llm(prompt, system=system, history=history)
            brain = "ollama"
        elif _openrouter_available():
            result = ask_openrouter(prompt, system=system, history=history)
            brain = "openrouter"
        else:
            result = ask_claude(prompt, system=system, history=history)
            brain = "claude"

    _set_cached(prompt, result)
    return result, brain
