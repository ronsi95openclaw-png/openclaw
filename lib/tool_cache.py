"""File-backed cache for expensive external API calls.

Wraps any callable with a TTL-keyed file cache stored in data/tool_cache/.
Cache files are JSON; each stores {ts, result}. Expired entries are
overwritten on next call.

Usage:
    from lib.tool_cache import cached

    closes = cached(
        key=f"candles:{instrument}:{timeframe}:{count}",
        ttl=3600,
        fn=fetch_closes,
        instrument, timeframe=timeframe, count=count,
    )

    prices = cached("coingecko:btc-eth-sol", ttl=60, fn=_fetch_prices)
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

_CACHE_DIR = Path(__file__).parent.parent / "data" / "tool_cache"


def cached(key: str, ttl: int, fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Return a cached result or call fn() and cache the result.

    Args:
        key:    Unique string key for this call (included in cache filename).
        ttl:    Time-to-live in seconds.
        fn:     Callable to invoke on cache miss.
        *args:  Positional args forwarded to fn.
        **kwargs: Keyword args forwarded to fn.

    Returns:
        The result of fn(*args, **kwargs), either fresh or from cache.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slot = _CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")

    if slot.exists():
        try:
            data = json.loads(slot.read_text(encoding="utf-8"))
            if time.time() - data["ts"] < ttl:
                return data["result"]
        except Exception:
            pass

    result = fn(*args, **kwargs)

    try:
        slot.write_text(
            json.dumps({"ts": time.time(), "result": result}, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass

    return result


def invalidate(key: str) -> bool:
    """Delete a cache entry by key. Returns True if it existed."""
    slot = _CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")
    if slot.exists():
        slot.unlink()
        return True
    return False


def cache_stats() -> dict:
    """Return count and total size of cache files."""
    if not _CACHE_DIR.exists():
        return {"entries": 0, "size_kb": 0}
    files = list(_CACHE_DIR.glob("*.json"))
    size  = sum(f.stat().st_size for f in files)
    return {"entries": len(files), "size_kb": round(size / 1024, 1)}
