"""Inference Scheduler — controls Ollama inference queuing and concurrency."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator, Optional

from system.resource_manager import ResourceManager

logger = logging.getLogger("openclaw.system.inference_scheduler")

_MAX_QUEUE_DEPTH = 10


class InferenceScheduler:
    """Controls Ollama inference request queuing and concurrency."""

    def __init__(
        self,
        max_concurrent: int = 2,
        queue_timeout_s: float = 30.0,
        resource_manager: Optional[ResourceManager] = None,
    ) -> None:
        self._semaphore = threading.Semaphore(max_concurrent)
        self._queue_depth: int = 0
        self._queue_lock = threading.Lock()
        self._queue_timeout_s = queue_timeout_s
        self._resource_manager = resource_manager
        self._max_concurrent = max_concurrent

    # ── Scheduling logic ───────────────────────────────────────────────────

    def can_schedule(self) -> bool:
        """Return False if resources are critical or the queue is too deep."""
        if self._resource_manager is not None:
            if self._resource_manager.should_defer_inference():
                return False
        with self._queue_lock:
            return self._queue_depth <= _MAX_QUEUE_DEPTH

    def get_queue_depth(self) -> int:
        with self._queue_lock:
            return self._queue_depth

    # ── Semaphore interface ────────────────────────────────────────────────

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a concurrency slot.

        Returns True on success, False on timeout or if scheduling is deferred.
        """
        if not self.can_schedule():
            logger.debug("InferenceScheduler: deferring — resources critical or queue full")
            return False

        effective_timeout = timeout if timeout is not None else self._queue_timeout_s
        with self._queue_lock:
            self._queue_depth += 1
        try:
            acquired = self._semaphore.acquire(timeout=effective_timeout)
            if not acquired:
                logger.debug("InferenceScheduler: timed out waiting for slot")
            return acquired
        except Exception:
            return False
        finally:
            with self._queue_lock:
                self._queue_depth = max(0, self._queue_depth - 1)

    def release(self) -> None:
        """Release a previously acquired concurrency slot."""
        try:
            self._semaphore.release()
        except Exception as exc:
            logger.warning("InferenceScheduler.release() error: %s", exc)

    # ── Context manager ────────────────────────────────────────────────────

    @contextmanager
    def slot(self, timeout: Optional[float] = None) -> Generator[bool, None, None]:
        """Context manager that yields True if a slot was acquired, False otherwise.

        Usage::
            with scheduler.slot() as ok:
                if ok:
                    run_inference()
        """
        acquired = self.acquire(timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self.release()
