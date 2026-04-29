"""Thread pool executor for CPU/IO-bound tasks that must not block the event loop.

Whisper transcription and FFmpeg encoding are synchronous and long-running.
Running them directly in an async Telegram handler blocks the entire bot.
Use run_in_pool() to offload them to a background thread.

Usage:
    from core.task_queue import run_in_pool

    reel_path, captions = await run_in_pool(pipeline.process, video_path,
                                             return_artifacts=True)
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

# Max 2 workers: one for video pipeline, one for any parallel task.
# Intentionally small — Whisper + FFmpeg are CPU-heavy.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="openclaw")


async def run_in_pool(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking function in the thread pool without blocking the event loop.

    Args:
        fn:      Synchronous callable.
        *args:   Positional args for fn.
        **kwargs: Keyword args for fn. NOTE: ThreadPoolExecutor.submit does not
                  accept kwargs directly — they are captured in a lambda.

    Returns:
        The return value of fn(*args, **kwargs).
    """
    loop = asyncio.get_event_loop()
    if kwargs:
        return await loop.run_in_executor(_POOL, lambda: fn(*args, **kwargs))
    return await loop.run_in_executor(_POOL, fn, *args)


def shutdown(wait: bool = True) -> None:
    """Gracefully shut down the pool. Call at bot exit."""
    _POOL.shutdown(wait=wait)
