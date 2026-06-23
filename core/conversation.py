"""Conversation history manager for ClawBot.

Persists the last 10 messages per chat to data/conversation_history.json.
Passed to brain.py so the LLM has context across turns.

History is keyed per Telegram *topic*: pass ``thread_id`` to keep each
workspace's memory isolated (a YouTube topic shouldn't see the trading topic's
context). Omit ``thread_id`` for plain chats — existing keys stay valid.

Usage:
    from core.conversation import add_message, get_history, clear_history
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

_DATA_DIR    = Path(__file__).parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "conversation_history.json"
MAX_HISTORY  = 10


def _key(chat_id: int, thread_id: Optional[int] = None) -> str:
    """History key for a chat or topic. Plain chats key on chat_id alone."""
    if thread_id is None:
        return str(chat_id)
    return f"{chat_id}:{thread_id}"


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


def add_message(
    chat_id: int, role: str, content: str, thread_id: Optional[int] = None
) -> None:
    """Append a message to the conversation history for a chat or topic.

    Args:
        chat_id: Telegram chat ID.
        role: "user" or "assistant".
        content: Message text.
        thread_id: Telegram topic (message_thread_id), if any — keeps each
            workspace's memory isolated.
    """
    data = _load()
    key = _key(chat_id, thread_id)
    history: List[dict] = data.get(key, [])
    history.append({"role": role, "content": content})
    # Keep only last MAX_HISTORY messages
    data[key] = history[-MAX_HISTORY:]
    _save(data)


def get_history(chat_id: int, thread_id: Optional[int] = None) -> List[dict]:
    """Return conversation history for a chat/topic as {role, content} dicts."""
    data = _load()
    return data.get(_key(chat_id, thread_id), [])


def clear_history(chat_id: int, thread_id: Optional[int] = None) -> None:
    """Clear conversation history for a chat or topic."""
    data = _load()
    data.pop(_key(chat_id, thread_id), None)
    _save(data)
