"""Route LLM outputs to the appropriate memory store.

Destinations:
    long_term  — durable insights: decisions, strategies, rules, configs
    working    — session-relevant context
    discard    — short or ephemeral responses

Files (append-only JSONL, one entry per line):
    memory/long_term.jsonl
    memory/working.jsonl
    memory/tier-usage.jsonl
    memory/soft-failures.jsonl
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

MemoryDest = Literal["long_term", "working", "discard"]

_MEMORY_DIR = Path(__file__).parent.parent / "memory"

_LONG_TERM_SIGNALS = re.compile(
    r"\b(decision|strategy|architecture|config|rule|protocol|"
    r"preference|always|never|guideline|setup|standard|enforce)\b",
    re.IGNORECASE,
)


def route_memory(output: str, prompt: str = "") -> MemoryDest:
    """Classify where a response should be stored."""
    if len(output) < 200:
        return "discard"
    if _LONG_TERM_SIGNALS.search(output) or _LONG_TERM_SIGNALS.search(prompt):
        return "long_term"
    return "working"


def write_memory(dest: MemoryDest, prompt: str, output: str, tier: str) -> None:
    """Append an entry to the destination JSONL file."""
    if dest == "discard":
        return
    _MEMORY_DIR.mkdir(exist_ok=True)
    entry = {
        "ts":     datetime.now(timezone.utc).isoformat(),
        "tier":   tier,
        "prompt": prompt[:200],
        "output": output[:500],
    }
    _append(_MEMORY_DIR / f"{dest}.jsonl", entry)


def log_tier_usage(
    tier: str,
    prompt_len: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Track which tier each request used — feeds adaptive tuning."""
    _MEMORY_DIR.mkdir(exist_ok=True)
    entry = {
        "ts":          datetime.now(timezone.utc).isoformat(),
        "tier":        tier,
        "prompt_len":  prompt_len,
        "tokens_in":   tokens_in,
        "tokens_out":  tokens_out,
    }
    _append(_MEMORY_DIR / "tier-usage.jsonl", entry)


def log_soft_failure(reason: str, tier: str, prompt_len: int) -> None:
    """Record a misroute or inefficiency for future classifier tuning."""
    _MEMORY_DIR.mkdir(exist_ok=True)
    entry = {
        "ts":         datetime.now(timezone.utc).isoformat(),
        "reason":     reason,
        "tier":       tier,
        "prompt_len": prompt_len,
    }
    _append(_MEMORY_DIR / "soft-failures.jsonl", entry)


def _append(path: Path, entry: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
