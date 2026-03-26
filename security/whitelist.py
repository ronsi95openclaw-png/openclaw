"""Chat ID whitelist for OpenClaw Telegram security.

Loads ALLOWED_CHAT_IDS from the environment (comma-separated integers).
Any Telegram message whose chat_id is not in the allowlist is silently ignored.

Usage:
    from security.whitelist import is_authorized

    if not is_authorized(update.effective_chat.id):
        return  # silently ignore
"""
from __future__ import annotations

import os
from typing import Set


def _load_allowed_ids() -> Set[int]:
    raw = os.getenv("ALLOWED_CHAT_ID", os.getenv("ALLOWED_CHAT_IDS", "")).strip()
    if not raw:
        return set()
    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


# Evaluated once at import time; restart the process to pick up env changes.
ALLOWED_CHAT_IDS: Set[int] = _load_allowed_ids()


def is_authorized(chat_id: int) -> bool:
    """Return True if chat_id is in the allowlist.

    If ALLOWED_CHAT_IDS is empty (not configured) every chat is denied,
    keeping the bot silent until an explicit allowlist is set.

    Args:
        chat_id: Telegram chat ID from the incoming update.

    Returns:
        True only when the chat_id appears in ALLOWED_CHAT_IDS.
    """
    return chat_id in ALLOWED_CHAT_IDS
