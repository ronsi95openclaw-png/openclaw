"""
ClawBot Failure Memory — Lessons Learned from Errors
=====================================================
Tracks bugs, errors, and their fixes so ClawBot doesn't repeat mistakes.

Storage: data/logs/lessons.json
Format per lesson:
    id, error, fix, file, timestamp, tags
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DATA_DIR     = Path(__file__).parent.parent / "data"
_LESSONS_FILE = _DATA_DIR / "logs" / "lessons.json"
_MAX_PREVIEW  = 120


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    if _LESSONS_FILE.exists():
        try:
            return json.loads(_LESSONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(lessons: list[dict]) -> None:
    _LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LESSONS_FILE.write_text(
        json.dumps(lessons, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Auto-tag helpers
# ---------------------------------------------------------------------------

_TAG_KEYWORDS = {
    "import":     ["import", "module", "package", "pip", "install"],
    "api":        ["api", "endpoint", "request", "response", "status code", "timeout"],
    "telegram":   ["telegram", "bot", "handler", "command", "update"],
    "trading":    ["trade", "order", "blofin", "crypto.com", "signal", "pnl"],
    "scheduler":  ["scheduler", "cron", "job", "schedule", "apscheduler"],
    "config":     ["env", "config", "settings", ".env", "os.getenv"],
    "syntax":     ["syntax", "indent", "unexpected", "invalid", "parse"],
    "async":      ["async", "await", "coroutine", "event loop"],
    "json":       ["json", "decode", "encode", "serializ", "key error"],
    "auth":       ["auth", "whitelist", "permission", "unauthorized", "token"],
}


def _auto_tags(text: str, extra_tags: list[str] | None = None) -> list[str]:
    lower = text.lower()
    tags = [tag for tag, kws in _TAG_KEYWORDS.items() if any(kw in lower for kw in kws)]
    if extra_tags:
        for t in extra_tags:
            if t and t not in tags:
                tags.append(t)
    return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_lesson(
    error: str,
    fix: str,
    file: str = "",
    tags: list[str] | None = None,
) -> dict:
    """
    Append a new lesson to lessons.json.

    Args:
        error: Description of the error or failure
        fix:   What resolved it
        file:  Optional file path where the issue occurred
        tags:  Optional extra tags; auto-tags are also added

    Returns:
        The saved lesson dict
    """
    lessons = _load()
    combined = f"{error} {fix}"
    lesson = {
        "id":        str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error":     error,
        "fix":       fix,
        "file":      file,
        "tags":      _auto_tags(combined, tags),
    }
    lessons.insert(0, lesson)  # newest first
    _save(lessons)
    return lesson


def get_lessons(query: str = "", limit: int = 10) -> list[dict]:
    """
    Return lessons, newest first.

    Args:
        query: Optional keyword — searches error, fix, file, tags fields
        limit: Max results to return
    """
    lessons = _load()
    if query:
        q = query.lower()
        lessons = [
            l for l in lessons
            if q in l.get("error", "").lower()
            or q in l.get("fix", "").lower()
            or q in l.get("file", "").lower()
            or any(q in t for t in l.get("tags", []))
        ]
    return lessons[:limit]


def format_lessons_message(lessons: list[dict], query: str = "") -> str:
    """Format a list of lessons as a Telegram HTML message."""
    if not lessons:
        if query:
            return f"🔍 No lessons found matching <code>{query}</code>."
        return (
            "📭 <b>No lessons logged yet.</b>\n\n"
            "Lessons are automatically recorded when errors are fixed.\n"
            "You can also add one manually:\n"
            "<code>/lessons add error | fix</code>"
        )

    header = (
        f"🔍 <b>Lessons matching \"{query}\"</b>"
        if query
        else "📚 <b>Lessons Learned</b>"
    )
    lines = [f"{header} ({len(lessons)} found)\n"]

    for l in lessons:
        ts      = l.get("timestamp", "")[:10]
        tags    = " ".join(f"#{t}" for t in l.get("tags", []))
        file_   = f"  📄 <code>{l['file']}</code>" if l.get("file") else ""
        error_p = l.get("error", "")[:_MAX_PREVIEW]
        fix_p   = l.get("fix", "")[:_MAX_PREVIEW]
        if len(l.get("error", "")) > _MAX_PREVIEW:
            error_p += "..."
        if len(l.get("fix", "")) > _MAX_PREVIEW:
            fix_p += "..."

        lines.append(
            f"🐛 <b>Error:</b> {error_p}\n"
            f"✅ <b>Fix:</b> {fix_p}\n"
            f"   📅 {ts}{file_}  {tags}"
        )

    lines.append("\n<i>/lessons [keyword] — search | /lessons add error | fix — add new</i>")
    return "\n\n".join(lines)
