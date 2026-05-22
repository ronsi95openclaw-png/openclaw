"""Qwen compression layer — sits between raw trade outcomes and Claude Opus.

Role in the pipeline:
    Trade closes → outcomes JSONL written
        ↓
    Qwen (qwen2.5:14b via Ollama, local, free)
        • reads raw trade: symbol / strategy / outcome / narrative / regime
        • compresses to a 2-sentence lesson (WHY it worked or failed, what to watch for)
        • result stored as "qwen_lesson" in the outcomes record
        ↓
    Claude Opus (daily)
        • receives pre-compressed lessons → sharper analysis, fewer tokens wasted

Gracefully skips if Ollama is unavailable — bot continues normally.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("openclaw.runtime.qwen_compressor")

_MODEL = "qwen2.5:14b"

_PROMPT_TEMPLATE = """\
You are a concise trading analyst. Summarise this closed trade in exactly 2 sentences.
Sentence 1: what caused the outcome (be specific — name the regime, indicator, and entry signal).
Sentence 2: one concrete rule to apply next time.

Trade:
  Symbol  : {symbol}
  Strategy: {strategy}
  Side    : {side}
  Outcome : {outcome} | PnL={pnl:+.4f}
  Regime  : {regime}
  Signal  : {signal_reason}
  Narrative: {narrative}

Reply with ONLY the 2 sentences. No labels, no markdown."""


def compress_trade(record: dict) -> str:
    """Generate a compressed 2-sentence lesson for one closed trade via Qwen.

    Returns empty string if Ollama is unavailable (non-fatal).
    """
    try:
        from core.brain import ask_llm
    except ImportError:
        return ""

    prompt = _PROMPT_TEMPLATE.format(
        symbol=record.get("symbol", "?"),
        strategy=record.get("strategy", "?"),
        side=record.get("side", "?").upper(),
        outcome=record.get("outcome", "?").upper(),
        pnl=float(record.get("pnl", 0)),
        regime=record.get("regime", "UNKNOWN"),
        signal_reason=record.get("signal_reason", "")[:120],
        narrative=record.get("narrative", "")[:200],
    )

    try:
        lesson = ask_llm(prompt, model=_MODEL)
        lesson = lesson.strip()
        logger.debug("Qwen lesson [%s/%s]: %s", record.get("symbol"), record.get("strategy"), lesson[:80])
        return lesson
    except Exception as exc:
        logger.debug("Qwen compression skipped (Ollama unavailable): %s", exc)
        return ""


def compress_batch(records: list[dict]) -> list[dict]:
    """Add 'qwen_lesson' to each record in-place. Returns the list."""
    for rec in records:
        if not rec.get("qwen_lesson"):
            rec["qwen_lesson"] = compress_trade(rec)
    return records
