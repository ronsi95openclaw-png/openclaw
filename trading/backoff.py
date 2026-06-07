"""
Exponential backoff for Crypto.com API calls.

Retries only on rate-limit (HTTP 429 / "rate limit" responses) and transient
network errors. Deterministic errors (bad params, "no candle data", auth
failures) are raised immediately so we don't waste time retrying something that
will never succeed.
"""
from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Callable

import requests

logger = logging.getLogger("clawbot.trading.backoff")

_RATE_LIMIT_PHRASES = ("rate limit", "too many requests", "429")


def is_rate_limit_error(exc: BaseException) -> bool:
    """True if the exception looks like an API rate-limit response."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return any(phrase in message for phrase in _RATE_LIMIT_PHRASES)


def is_retryable(exc: BaseException) -> bool:
    """True if retrying might help: rate-limit or transient network error."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return is_rate_limit_error(exc)


def with_backoff(
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    sleeper: Callable[[float], None] = time.sleep,
):
    """Decorator: retry the wrapped call with exponential backoff + jitter."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last = attempt == max_retries - 1
                    if last or not is_retryable(exc):
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay) + random.uniform(0, base_delay)
                    logger.warning(
                        "%s: %s — retry %d/%d in %.1fs",
                        func.__name__, exc, attempt + 1, max_retries - 1, delay,
                    )
                    sleeper(delay)
            raise RuntimeError(f"{func.__name__}: retries exhausted")  # pragma: no cover

        return wrapper

    return decorator
