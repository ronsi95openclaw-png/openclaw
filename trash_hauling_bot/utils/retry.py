import functools
import logging
import time
from typing import Callable, Tuple, Type

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """Exponential-backoff retry decorator for transient failures (e.g. Google API calls)."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts:
                        raise
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        func.__qualname__, attempt, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
                    wait *= backoff
        return wrapper
    return decorator
