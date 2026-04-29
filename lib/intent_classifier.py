"""Three-tier intent classifier for the OpenClaw brain router.

Tiers:
    T1 chat            — trivial / casual → Ollama
    T2 cheap_reasoning — analytical but bounded → Ollama
    T3 precision       — high-stakes / architectural → Claude
"""
from __future__ import annotations

import re
from typing import Literal

Tier = Literal["chat", "cheap_reasoning", "precision"]

_PRECISION = re.compile(
    r"\b(design|architect|system|framework|strategy|plan|govern|"
    r"evaluate|assessment|forecast|predict|full report|audit|"
    r"implementation|scalab|production|optim)\w*\b",
    re.IGNORECASE,
)

_CHEAP = re.compile(
    r"\b(summarize|analyse|analyze|explain|compare|breakdown|"
    r"research|overview|detail|pros.cons|deep.dive|investigate|"
    r"recommendation|suggest)\w*\b",
    re.IGNORECASE,
)

_CONFIDENCE_THRESHOLD = 0.75


def classify_intent(prompt: str, confidence_override: float = 1.0) -> Tier:
    """Return the routing tier for a prompt.

    Args:
        prompt: Raw (already optimized) user prompt.
        confidence_override: If below _CONFIDENCE_THRESHOLD, escalate one tier.
    """
    words = prompt.split()
    n = len(words)

    if n < 8 and not _PRECISION.search(prompt) and not _CHEAP.search(prompt):
        base: Tier = "chat"
    elif _PRECISION.search(prompt):
        base = "precision"
    elif _CHEAP.search(prompt) or n >= 50:
        base = "cheap_reasoning"
    else:
        base = "chat"

    if confidence_override < _CONFIDENCE_THRESHOLD:
        base = _escalate(base)

    return base


def _escalate(tier: Tier) -> Tier:
    if tier == "chat":
        return "cheap_reasoning"
    return "precision"


def tier_to_complexity(tier: Tier) -> str:
    """Map a Tier to the brain.py complexity string."""
    return "complex" if tier == "precision" else "simple"
