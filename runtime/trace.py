"""Distributed trace context — propagates a trace_id through all decisions."""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class TraceContext:
    trace_id:   str
    parent_id:  Optional[str]
    source:     str       # "scan_loop" | "telegram" | "replay" | "test"
    symbol:     Optional[str]
    strategy:   Optional[str]
    started_at: datetime  = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:   Dict[str, Any] = field(default_factory=dict)

    def child(self, source: str, **kwargs) -> "TraceContext":
        return TraceContext(
            trace_id=str(uuid.uuid4()),
            parent_id=self.trace_id,
            source=source,
            symbol=kwargs.get("symbol", self.symbol),
            strategy=kwargs.get("strategy", self.strategy),
            metadata=kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id":  self.trace_id,
            "parent_id": self.parent_id,
            "source":    self.source,
            "symbol":    self.symbol,
            "strategy":  self.strategy,
            "started_at": self.started_at.isoformat(),
            "metadata":  self.metadata,
        }


# Thread-local active trace context
_local = threading.local()


def current_trace() -> Optional[TraceContext]:
    return getattr(_local, "ctx", None)


def new_trace(source: str, **kwargs) -> TraceContext:
    ctx = TraceContext(
        trace_id=str(uuid.uuid4()),
        parent_id=None,
        source=source,
        symbol=kwargs.get("symbol"),
        strategy=kwargs.get("strategy"),
        metadata={k: v for k, v in kwargs.items() if k not in ("symbol", "strategy")},
    )
    _local.ctx = ctx
    return ctx


@contextmanager
def trace_scope(source: str, **kwargs):
    """Context manager that sets the active trace and clears on exit."""
    ctx = new_trace(source, **kwargs)
    try:
        yield ctx
    finally:
        _local.ctx = None
