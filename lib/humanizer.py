"""Trim AI responses to feel human on Telegram, not wall-of-text robotic.

Rules:
  - Short responses (< 200 chars): return as-is
  - Long responses: keep first 8 lines, append a "reply for more" hint
  - Hard cap at 1200 chars to respect Telegram's rendering limits
  - Never truncate mid-sentence if avoidable

Usage:
    from lib.humanizer import humanize

    await msg.reply_text(humanize(response_text), parse_mode="HTML")
"""
from __future__ import annotations

_SHORT_THRESHOLD = 200
_MAX_LINES       = 8
_MAX_CHARS       = 1200
_MORE_HINT       = "\n<i>…reply for more</i>"


def humanize(text: str) -> str:
    """Trim text to a human-friendly length for Telegram.

    For short responses returns unchanged. For long responses, trims to
    _MAX_LINES lines or _MAX_CHARS characters (whichever is shorter) and
    appends a "reply for more" nudge.
    """
    if len(text) <= _SHORT_THRESHOLD:
        return text

    lines = text.split("\n")

    if len(lines) > _MAX_LINES:
        trimmed = "\n".join(lines[:_MAX_LINES])
        return (trimmed[:_MAX_CHARS] + _MORE_HINT) if len(trimmed) > _MAX_CHARS else trimmed + _MORE_HINT

    if len(text) > _MAX_CHARS:
        # Find last sentence boundary before the cap
        cut = text[:_MAX_CHARS].rfind(". ")
        cut = cut if cut > _MAX_CHARS // 2 else _MAX_CHARS
        return text[:cut + 1] + _MORE_HINT

    return text
