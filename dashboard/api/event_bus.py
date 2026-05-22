"""Thread-safe event bus bridging the sync bot threads → async WebSocket layer.

The trading bot calls publish() from its sync thread.
FastAPI WebSocket handlers subscribe() in async context and await events.
"""
from __future__ import annotations

import asyncio
import json
import logging
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.dashboard.event_bus")

_EVENT_TYPES = {
    "state_update",   # full bot snapshot (every 1s when changed)
    "trade_open",     # new position opened
    "trade_close",    # position closed with outcome
    "signal",         # strategy signal fired (blocked or approved)
    "regime",         # regime classification updated
    "capital_state",  # SAFE/DEFENSIVE/CRITICAL/EMERGENCY_HALT transition
    "analysis",       # Claude Opus daily report ready
    "system_health",  # capability matrix snapshot
}


class EventBus:
    def __init__(self, maxsize: int = 500):
        self._queues:  List[asyncio.Queue] = []
        self._lock     = Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._maxsize  = maxsize

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Thread-safe publish — callable from sync bot threads."""
        # Snapshot loop reference under lock to prevent TOCTOU None-deref
        with self._lock:
            loop   = self._loop
            queues = list(self._queues)
        if loop is None or loop.is_closed():
            return
        payload = json.dumps({"type": event_type, "data": data}, default=str)
        for q in queues:
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except asyncio.QueueFull:
                logger.warning(
                    "EventBus: queue full for event '%s' — WebSocket client too slow, dropping event",
                    event_type,
                )
            except Exception as exc:
                logger.debug("EventBus: publish error for event '%s': %s", event_type, exc)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._queues)


# Module-level singleton
_bus = EventBus()


def get_bus() -> EventBus:
    return _bus
