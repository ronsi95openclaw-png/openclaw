"""Compute deltas between state snapshots to minimize payload size."""
from __future__ import annotations

from typing import Any


def create_delta(prev: Any, next_val: Any) -> dict:
    """Return only what changed between prev and next_val.

    For dicts: returns changed keys and list of removed keys.
    For scalars/strings: returns the new value directly.
    First call (prev=None): returns full state.
    """
    if prev is None:
        return {"full": next_val}
    if isinstance(prev, dict) and isinstance(next_val, dict):
        changed = {k: v for k, v in next_val.items() if prev.get(k) != v}
        removed = [k for k in prev if k not in next_val]
        return {"changed": changed, "removed": removed}
    return {"changed": next_val}


def apply_delta(base: dict, delta: dict) -> dict:
    """Merge a delta back onto a base dict."""
    result = dict(base)
    for k in delta.get("removed", []):
        result.pop(k, None)
    result.update(delta.get("changed", {}))
    return result
