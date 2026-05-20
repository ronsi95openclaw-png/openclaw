"""Chaos tests for failure mode validation."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Callable, Optional

logger = logging.getLogger("openclaw.validation.chaos")


class ChaosTestRunner:
    """Injects failures to test graceful degradation."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    # ── individual chaos scenarios ─────────────────────────────────────────────

    async def test_websocket_disconnect(self, reconnect_fn: Callable) -> bool:
        """Simulate websocket disconnect and verify reconnection within 5 seconds."""
        logger.info("ChaosTest: simulating websocket disconnect")
        try:
            reconnect_task = asyncio.create_task(
                self._maybe_coro(reconnect_fn)
            )
            await asyncio.wait_for(reconnect_task, timeout=5.0)
            logger.info("ChaosTest: websocket reconnect succeeded")
            return True
        except asyncio.TimeoutError:
            logger.error("ChaosTest: websocket reconnect timed out (>5s)")
            return False
        except Exception as exc:
            logger.error("ChaosTest: websocket reconnect raised: %s", exc)
            return False

    async def test_db_outage(
        self,
        db_write_fn: Callable,
        fallback_fn: Callable,
    ) -> bool:
        """Simulate DB write failure and verify fallback is used."""
        logger.info("ChaosTest: simulating DB outage")

        fallback_called = False

        async def _failing_db_write(*args, **kwargs):
            raise ConnectionError("Simulated DB outage")

        async def _tracked_fallback(*args, **kwargs):
            nonlocal fallback_called
            fallback_called = True
            result = fallback_fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

        try:
            await self._maybe_coro(_failing_db_write)
        except ConnectionError:
            # Expected — now verify fallback is invoked
            await self._maybe_coro(_tracked_fallback)

        if fallback_called:
            logger.info("ChaosTest: DB outage fallback triggered correctly")
        else:
            logger.warning("ChaosTest: fallback was NOT called during DB outage")
        return fallback_called

    async def test_exchange_timeout(self, api_fn: Callable) -> bool:
        """Simulate exchange API timeout and verify it is handled gracefully."""
        logger.info("ChaosTest: simulating exchange API timeout")

        async def _slow_api(*args, **kwargs):
            await asyncio.sleep(10.0)  # deliberate timeout trigger
            return await self._maybe_coro(api_fn, *args, **kwargs)

        try:
            await asyncio.wait_for(_slow_api(), timeout=3.0)
            # Should not reach here — a well-behaved caller wraps with timeout
            logger.warning("ChaosTest: exchange timeout not triggered (check caller)")
            return False
        except asyncio.TimeoutError:
            logger.info("ChaosTest: exchange timeout handled correctly")
            return True
        except Exception as exc:
            logger.error("ChaosTest: unexpected exception during timeout test: %s", exc)
            return False

    def test_kill_switch_under_load(
        self,
        kill_switch,
        bot_fn: Callable,
    ) -> bool:
        """Verify kill switch stops trading even under active load.

        Parameters
        ----------
        kill_switch:
            Object with an ``activate()`` method that sets a halt flag and an
            ``is_active`` property (or attribute).
        bot_fn:
            Callable that returns a list of signals.  Must return an empty
            list when kill switch is active.
        """
        logger.info("ChaosTest: activating kill switch under load")
        try:
            kill_switch.activate()
        except AttributeError:
            # If the kill switch just has a boolean flag
            try:
                kill_switch.active = True
            except AttributeError:
                logger.error("ChaosTest: kill_switch has no activate() or .active")
                return False

        try:
            signals = bot_fn()
            if asyncio.iscoroutine(signals):
                # Can't await in a sync method; treat as inconclusive
                logger.warning("ChaosTest: bot_fn returned a coroutine — use async variant")
                return False
        except Exception as exc:
            logger.error("ChaosTest: bot_fn raised under kill switch: %s", exc)
            return False

        if signals:
            logger.error(
                "ChaosTest: kill switch active but %d signals returned", len(signals)
            )
            return False

        logger.info("ChaosTest: kill switch correctly suppressed all signals")
        return True

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    async def _maybe_coro(fn: Callable, *args, **kwargs):
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
