"""
ClawBot Knowledge Base — Persistent Project Notes
===================================================
Saves important conversations, decisions, and ideas related to
what we're building with OpenClaw. Unlike conversation history
(which expires after 4h), these notes persist permanently.

Commands:
    /save [optional title]  — save last exchange or a custom note
    /notes                  — list recent saved notes
    /notes [search term]    — search saved notes
    /notes delete <id>      — delete a note

Storage: data/knowledge/notes.json
Format per note:
    id, timestamp, title, content, tags, source (telegram/manual)
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DATA_DIR    = Path(__file__).parent.parent / "data"
_NOTES_FILE  = _DATA_DIR / "knowledge" / "notes.json"
_MAX_PREVIEW = 120   # chars shown in /notes list


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    if _NOTES_FILE.exists():
        try:
            return json.loads(_NOTES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(notes: list[dict]) -> None:
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTES_FILE.write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Auto-tag using LLM
# ---------------------------------------------------------------------------

_TAG_KEYWORDS = {
    "trading":    ["trade", "signal", "rsi", "macd", "entry", "exit", "stop", "tp", "sl", "pnl"],
    "strategy":   ["strategy", "backtest", "ema", "bollinger", "breakout", "bias"],
    "blofin":     ["blofin", "futures", "perp", "leverage", "margin"],
    "crypto.com": ["crypto.com", "dca", "spot", "portfolio"],
    "agent":      ["agent", "news filter", "sheets", "code review", "scheduler"],
    "idea":       ["idea", "plan", "build", "feature", "add", "want", "could", "should"],
    "bug":        ["bug", "fix", "error", "broken", "issue", "fail", "crash"],
    "setup":      ["install", "setup", "config", "env", "api key", "token"],
}


def _auto_tags(content: str) -> list[str]:
    """Detect relevant tags from content keywords."""
    lower = content.lower()
    return [tag for tag, kws in _TAG_KEYWORDS.items() if any(kw in lower for kw in kws)]


def _auto_title(content: str) -> str:
    """Extract a short title from the first meaningful line of content."""
    for line in content.splitlines():
        line = line.strip()
        if len(line) > 10:
            return line[:60] + ("..." if len(line) > 60 else "")
    return content[:60]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_note(
    content: str,
    title: Optional[str] = None,
    source: str = "telegram",
) -> dict:
    """
    Save a note to the knowledge base.

    Args:
        content: The text to save (conversation excerpt, idea, decision)
        title:   Optional title — auto-generated from content if not provided
        source:  Where it came from ("telegram", "manual", "auto")

    Returns:
        The saved note dict
    """
    notes = _load()
    note  = {
        "id":        str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title":     title or _auto_title(content),
        "content":   content,
        "tags":      _auto_tags(content),
        "source":    source,
    }
    notes.insert(0, note)   # newest first
    _save(notes)
    return note


def get_notes(limit: int = 10, search: Optional[str] = None) -> list[dict]:
    """
    Return saved notes, newest first.

    Args:
        limit:  Max notes to return
        search: Optional search term (matches title, content, tags)
    """
    notes = _load()
    if search:
        q = search.lower()
        notes = [
            n for n in notes
            if q in n.get("title", "").lower()
            or q in n.get("content", "").lower()
            or any(q in t for t in n.get("tags", []))
        ]
    return notes[:limit]


def get_note_by_id(note_id: str) -> Optional[dict]:
    """Return a single note by its short ID."""
    for note in _load():
        if note.get("id") == note_id:
            return note
    return None


def delete_note(note_id: str) -> bool:
    """Delete a note by ID. Returns True if found and deleted."""
    notes = _load()
    before = len(notes)
    notes  = [n for n in notes if n.get("id") != note_id]
    if len(notes) < before:
        _save(notes)
        return True
    return False


def save_conversation_exchange(
    user_message: str,
    bot_response: str,
    title: Optional[str] = None,
) -> dict:
    """Save a full user↔bot exchange as a note."""
    content = f"You: {user_message}\n\nClawBot: {bot_response}"
    return save_note(content, title=title, source="telegram")


# ---------------------------------------------------------------------------
# Telegram formatters
# ---------------------------------------------------------------------------

def format_notes_list(notes: list[dict], search: Optional[str] = None) -> str:
    """Format a list of notes as a Telegram HTML message."""
    if not notes:
        if search:
            return f"🔍 No notes found matching <code>{search}</code>."
        return (
            "📭 <b>No saved notes yet.</b>\n\n"
            "Use /save after any conversation to save it.\n"
            "Or: /save your idea here — saves a custom note."
        )

    header = f"🔍 <b>Notes matching \"{search}\"</b>" if search else "📓 <b>Saved Notes</b>"
    lines  = [f"{header} ({len(notes)} found)\n"]

    for n in notes:
        ts      = n.get("timestamp", "")[:10]
        tags    = " ".join(f"#{t}" for t in n.get("tags", []))
        preview = n.get("content", "")[:_MAX_PREVIEW].replace("\n", " ")
        if len(n.get("content", "")) > _MAX_PREVIEW:
            preview += "..."

        lines.append(
            f"🗒 <b>{n['title']}</b>  <i>[{n['id']}]</i>\n"
            f"   📅 {ts}  {tags}\n"
            f"   <i>{preview}</i>"
        )

    lines.append("\n<i>/notes [search] — search | /notes delete &lt;id&gt; — remove</i>")
    return "\n\n".join(lines)


def format_note_saved(note: dict) -> str:
    """Confirmation message when a note is saved."""
    tags = " ".join(f"#{t}" for t in note.get("tags", [])) or "none"
    return (
        f"✅ <b>Note saved!</b>\n\n"
        f"📝 <b>{note['title']}</b>\n"
        f"🏷 Tags: {tags}\n"
        f"🔑 ID: <code>{note['id']}</code>\n\n"
        f"<i>View all: /notes | Search: /notes [topic]</i>"
    )
