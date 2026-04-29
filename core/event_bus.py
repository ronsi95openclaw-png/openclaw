"""Lightweight async event bus — decouples producers from consumers.

All handlers internally call brain(). This lets Telegram, trading, and
any future SaaS module emit events without importing each other.

Usage:
    from core.event_bus import register, emit

    register("user.message", handle_user_message)
    register("market.update", handle_market_update)
    register("signal.generated", handle_signal)

    # Fire from anywhere:
    result = await emit("user.message", {"text": "...", "chat_id": 123})
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger("openclaw.event_bus")

# event_name → handler callable (sync or async)
_handlers: dict[str, Callable] = {}


def register(event: str, fn: Callable) -> None:
    """Register a handler for an event. One handler per event (last wins)."""
    _handlers[event] = fn
    logger.debug(f"Registered handler for '{event}'")


def unregister(event: str) -> None:
    _handlers.pop(event, None)


async def emit(event: str, payload: Any = None) -> Any:
    """Fire an event and return the handler result.

    Supports both sync and async handlers.
    Returns None if no handler is registered.
    """
    fn = _handlers.get(event)
    if fn is None:
        logger.debug(f"No handler for event '{event}' — skipped")
        return None
    try:
        if asyncio.iscoroutinefunction(fn):
            return await fn(payload)
        return fn(payload)
    except Exception as exc:
        logger.error(f"Event handler '{event}' raised: {exc}")
        raise


def list_events() -> list[str]:
    """Return all registered event names."""
    return list(_handlers.keys())
