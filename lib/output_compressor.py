"""Strip LLM filler from the start of responses and collapse blank lines."""
from __future__ import annotations

import re

_FILLER_LEAD = re.compile(
    r"^(Sure[,!.]?|Of course[,!.]?|Absolutely[,!.]?|"
    r"Here(?:'s| is)[^.]*?\.|Great question[,!.]?|Certainly[,!.]?|"
    r"I(?:'d| would) be happy to[^.]*?\.|No problem[,!.]?)\s*",
    re.IGNORECASE,
)

_MULTI_BLANK = re.compile(r"\n{3,}")


def compress_output(text: str) -> str:
    """Remove filler opener and collapse excess blank lines."""
    text = _FILLER_LEAD.sub("", text, count=1)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()
