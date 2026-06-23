"""Telegram-topic workspaces for ClawBot.

Each Telegram *topic* (a thread inside a group) becomes a separate workspace
with its own operating rule, so ClawBot stops being one messy junk-drawer chat
and starts working "inside the right room".

A workspace is just an operating rule layered on top of the base persona
(``CLAWBOT_SYSTEM``). When you message ClawBot inside a topic, it doesn't have
to guess which mode it's in — the topic already tells it.

Topic identity:
    A Telegram group topic is identified by ``(chat_id, message_thread_id)``.
    Plain (non-topic) chats have ``message_thread_id = None`` and fall back to
    the ``general`` catch-all workspace.

Persistence (data/workspaces.json):
    {
      "bindings": { "<chat_id>:<thread_id>": "trading" },
      "rules":    { "<chat_id>:<thread_id>": "custom operating rule text" }
    }

Public API:
    from core.workspaces import (
        resolve, list_workspaces, bind_topic, set_rule,
        clear_rule, system_prompt, topic_key,
    )

This module intentionally has no heavy dependencies (no anthropic / ollama),
so the base persona is passed in by the caller via ``system_prompt(...)``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_DATA_DIR = Path(__file__).parent.parent / "data"
_STORE_FILE = _DATA_DIR / "workspaces.json"


@dataclass(frozen=True)
class Workspace:
    """A named operating room with its own rule."""

    key: str
    name: str
    emoji: str
    rule: str

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}"


# ── Default workspace templates ─────────────────────────────────────────────
# The three rooms the video recommends (main work / content / admin) plus a
# general catch-all. Tuned for the OpenClaw brand: "main work" is trading.

_DEFAULT_WORKSPACES: Dict[str, Workspace] = {
    "general": Workspace(
        key="general",
        name="General",
        emoji="💬",
        rule=(
            "This is the general catch-all room. Answer anything that doesn't "
            "belong to a more specific workspace. Stay sharp and concise."
        ),
    ),
    "trading": Workspace(
        key="trading",
        name="Trading",
        emoji="📈",
        rule=(
            "This is the trading room. Before suggesting any trade, check live "
            "market data, the current trading mode (DEMO/LIVE), and recent trade "
            "decisions. Flag risk explicitly and never imply a trade is executed "
            "until it is confirmed. Think entries, exits, position sizing — not "
            "content or admin."
        ),
    ),
    "content": Workspace(
        key="content",
        name="Content",
        emoji="🎬",
        rule=(
            "This is the content / research room for the OpenClaw brand. Before "
            "suggesting anything, consider what's already been posted, what was "
            "rejected, what's currently trending, and whether it can actually be "
            "made. Return one clear next move with the reason it matters now."
        ),
    ),
    "admin": Workspace(
        key="admin",
        name="Admin",
        emoji="🗂️",
        rule=(
            "This is the admin / productivity room. Organize tasks, draft "
            "responses, and summarize — but always ask before sending anything "
            "externally. Keep output actionable and short."
        ),
    ),
}

DEFAULT_WORKSPACE_KEY = "general"


def builtin_workspaces() -> List[Workspace]:
    """Return the built-in workspace templates."""
    return list(_DEFAULT_WORKSPACES.values())


# ── Topic identity ──────────────────────────────────────────────────────────

def topic_key(chat_id: int, thread_id: Optional[int] = None) -> str:
    """Stable key for a Telegram topic.

    Plain chats (no thread) key on the chat id alone; topics append the thread.
    """
    if thread_id is None:
        return str(chat_id)
    return f"{chat_id}:{thread_id}"


# ── Store helpers ───────────────────────────────────────────────────────────

def _load(path: Optional[Path] = None) -> dict:
    p = path or _STORE_FILE
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data.setdefault("bindings", {})
            data.setdefault("rules", {})
            return data
        except Exception:
            pass
    return {"bindings": {}, "rules": {}}


def _save(data: dict, path: Optional[Path] = None) -> None:
    p = path or _STORE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Public API ──────────────────────────────────────────────────────────────

def list_workspaces() -> List[Workspace]:
    """All workspaces a user can bind a topic to (the built-in templates)."""
    return builtin_workspaces()


def resolve(
    chat_id: int,
    thread_id: Optional[int] = None,
    *,
    path: Optional[Path] = None,
) -> Workspace:
    """Return the active :class:`Workspace` for a topic.

    Falls back to the ``general`` workspace when the topic isn't bound. A
    per-topic custom rule (set via :func:`set_rule`) overrides the template
    rule while keeping the workspace identity/name.
    """
    data = _load(path)
    key = topic_key(chat_id, thread_id)

    ws_key = data["bindings"].get(key, DEFAULT_WORKSPACE_KEY)
    base = _DEFAULT_WORKSPACES.get(ws_key, _DEFAULT_WORKSPACES[DEFAULT_WORKSPACE_KEY])

    custom_rule = data["rules"].get(key)
    if custom_rule:
        return Workspace(key=base.key, name=base.name, emoji=base.emoji, rule=custom_rule)
    return base


def bind_topic(
    chat_id: int,
    thread_id: Optional[int],
    ws_key: str,
    *,
    path: Optional[Path] = None,
) -> Workspace:
    """Assign a topic to a workspace template. Returns the resolved workspace."""
    if ws_key not in _DEFAULT_WORKSPACES:
        raise ValueError(
            f"Unknown workspace '{ws_key}'. "
            f"Choose one of: {', '.join(_DEFAULT_WORKSPACES)}"
        )
    data = _load(path)
    data["bindings"][topic_key(chat_id, thread_id)] = ws_key
    _save(data, path)
    return resolve(chat_id, thread_id, path=path)


def set_rule(
    chat_id: int,
    thread_id: Optional[int],
    rule: str,
    *,
    path: Optional[Path] = None,
) -> Workspace:
    """Override the operating rule for a specific topic."""
    rule = rule.strip()
    if not rule:
        raise ValueError("Operating rule cannot be empty.")
    data = _load(path)
    data["rules"][topic_key(chat_id, thread_id)] = rule
    _save(data, path)
    return resolve(chat_id, thread_id, path=path)


def clear_rule(
    chat_id: int,
    thread_id: Optional[int],
    *,
    path: Optional[Path] = None,
) -> Workspace:
    """Drop a topic's custom rule, reverting to its workspace template rule."""
    data = _load(path)
    data["rules"].pop(topic_key(chat_id, thread_id), None)
    _save(data, path)
    return resolve(chat_id, thread_id, path=path)


def system_prompt(base: str, workspace: Workspace) -> str:
    """Layer a workspace's operating rule on top of the base persona prompt."""
    return (
        f"{base.rstrip()}\n\n"
        f"## Active workspace: {workspace.label}\n"
        f"{workspace.rule}"
    )
