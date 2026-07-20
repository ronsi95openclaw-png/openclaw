"""Conversation history manager for ClawBot.

Persists the last 10 messages per chat to data/conversation_history.json.
Passed to brain.py so the LLM has context across turns.

Auto-expires after 4 hours of inactivity so old context never bleeds into
new conversations (e.g. yesterday's grid trading advice won't confuse today's queries).

Usage:
    from core.conversation import add_message, get_history, clear_history
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

_DATA_DIR     = Path(__file__).parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "conversation_history.json"
MAX_HISTORY   = 10
TTL_SECONDS   = 4 * 3600   # 4 hours — auto-clear stale context


def _load() -> dict:
    if _HISTORY_FILE.exists():
        try:
            return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _normalize(raw) -> dict:
    """Migrate old list format → new dict format with last_active."""
    if isinstance(raw, list):
        # Old format — treat as expired (no timestamp available)
        return {"messages": [], "last_active": 0}
    if isinstance(raw, dict):
        return raw
    return {"messages": [], "last_active": 0}


def add_message(chat_id: int, role: str, content: str) -> None:
    """Append a message to the conversation history for a chat."""
    data  = _load()
    key   = str(chat_id)
    entry = _normalize(data.get(key, {"messages": [], "last_active": 0}))

    # Auto-expire if inactive for TTL_SECONDS
    if time.time() - entry.get("last_active", 0) > TTL_SECONDS:
        entry = {"messages": [], "last_active": 0}

    entry["messages"].append({"role": role, "content": content})
    entry["messages"]    = entry["messages"][-MAX_HISTORY:]
    entry["last_active"] = time.time()
    data[key] = entry
    _save(data)


def get_history(chat_id: int) -> List[dict]:
    """Return conversation history for a chat. Returns [] if expired."""
    data  = _load()
    key   = str(chat_id)
    entry = _normalize(data.get(key, {}))

    # Expire stale sessions
    if time.time() - entry.get("last_active", 0) > TTL_SECONDS:
        return []

    return entry.get("messages", [])


def clear_history(chat_id: int) -> None:
    """Clear conversation history for a chat."""
    data = _load()
    data.pop(str(chat_id), None)
    _save(data)
