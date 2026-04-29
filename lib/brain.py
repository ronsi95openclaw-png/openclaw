"""Unified execution spine — single entry point for all AI calls.

Every caller (Telegram, trading, SaaS) calls brain() here instead of
ask_hybrid() directly. This layer adds:
  - Purpose-based tier forcing
  - Working memory injection (analysis + high_stakes only)
  - Delta prefix for update-style prompts
  - Structured return dict: {text, brain, purpose}

Purpose → forced tier:
  chat            → auto-classify  (T1/T2/T3)
  analysis        → cheap_reasoning (Ollama)
  high_stakes     → precision       (Claude Haiku)
  caption         → chat            (Ollama, no memory)
  market_summary  → chat            (Ollama, no memory)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from core.brain import ask_hybrid
from lib.input_optimizer import optimize_input

_MEMORY_DIR = Path(__file__).parent.parent / "memory"

# Purposes that benefit from recent working memory injection
_INJECT_MEMORY_FOR = {"analysis", "high_stakes"}

# Purpose → force arg for ask_hybrid
_PURPOSE_FORCE: dict[str, Optional[str]] = {
    "chat":           None,       # let classifier decide
    "analysis":       "simple",   # Ollama sufficient
    "high_stakes":    "complex",  # Claude Haiku required
    "caption":        "simple",   # creative, Ollama only
    "market_summary": "simple",   # fresh data, Ollama only
}


def get_recent_memory(limit: int = 5) -> str:
    """Return last N working memory entries as plain text context."""
    path = _MEMORY_DIR / "working.jsonl"
    if not path.exists():
        return ""
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        texts = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
                snippet = entry.get("output", "")[:200].strip()
                if snippet:
                    texts.append(snippet)
            except Exception:
                pass
        return "\n".join(texts)
    except Exception:
        return ""


def brain(
    prompt: str,
    purpose: str = "chat",
    system: Optional[str] = None,
    history: Optional[list] = None,
    delta: Optional[Any] = None,
) -> dict:
    """Single execution entry point for all AI calls in OpenClaw.

    Args:
        prompt:  Raw input text.
        purpose: Routing hint — chat | analysis | high_stakes | caption | market_summary
        system:  Override system prompt (optional).
        history: Conversation history list (optional).
        delta:   Dict of changed state; if set, prepended as context.

    Returns:
        {"text": str, "brain": str, "purpose": str}
    """
    # 1. Optimize raw input first
    clean = optimize_input(prompt)

    # 2. Delta injection — prefix with changed state
    if delta is not None:
        clean = f"Update based on changes:\n{json.dumps(delta, default=str)}\n\n{clean}"

    # 3. Working memory injection — only for analytical/high-stakes calls
    if purpose in _INJECT_MEMORY_FOR:
        mem = get_recent_memory(limit=5)
        if mem:
            clean = f"[Recent context]\n{mem}\n\n[Request]\n{clean}"

    # 4. Resolve force tier from purpose
    force = _PURPOSE_FORCE.get(purpose)

    # 5. Route through unified ask_hybrid pipeline
    text, brain_used = ask_hybrid(
        prompt=clean,
        system=system,
        history=history,
        force=force,
    )

    return {"text": text, "brain": brain_used, "purpose": purpose}
