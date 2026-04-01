"""Conversation history manager for ClawBot.

Persists the last 10 messages per chat to data/conversation_history.json.
Passed to brain.py so the LLM has context across turns.

Usage:
    from core.conversation import add_message, get_history, clear_history
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

_DATA_DIR    = Path(__file__).parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "conversation_history.json"
MAX_HISTORY  = 10


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


def add_message(chat_id: int, role: str, content: str) -> None:
    """Append a message to the conversation history for a chat.

    Args:
        chat_id: Telegram chat ID (used as key).
        role: "user" or "assistant".
        content: Message text.
    """
    data = _load()
    key = str(chat_id)
    history: List[dict] = data.get(key, [])
    history.append({"role": role, "content": content})
    # Keep only last MAX_HISTORY messages
    data[key] = history[-MAX_HISTORY:]
    _save(data)


def get_history(chat_id: int) -> List[dict]:
    """Return conversation history for a chat as a list of {role, content} dicts."""
    data = _load()
    return data.get(str(chat_id), [])


def clear_history(chat_id: int) -> None:
    """Clear conversation history for a chat."""
    data = _load()
    data.pop(str(chat_id), None)
    _save(data)
