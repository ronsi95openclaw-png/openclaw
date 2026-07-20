"""Simple in-memory rate limiter for Telegram bot commands."""
from collections import defaultdict
from datetime import datetime, timezone
import threading

_lock = threading.Lock()
_windows: dict[int, list[float]] = defaultdict(list)

MAX_COMMANDS = 30        # per window
WINDOW_SECONDS = 60      # 1 minute window
BURST_MAX = 10           # max in 5 seconds
BURST_WINDOW = 5


def is_rate_limited(chat_id: int) -> bool:
    """Return True if chat_id has exceeded rate limit."""
    now = datetime.now(timezone.utc).timestamp()
    with _lock:
        times = _windows[chat_id]
        # Clean old entries
        times[:] = [t for t in times if now - t < WINDOW_SECONDS]
        # Check burst
        recent = [t for t in times if now - t < BURST_WINDOW]
        if len(recent) >= BURST_MAX:
            return True
        # Check window
        if len(times) >= MAX_COMMANDS:
            return True
        times.append(now)
        return False


def get_rate_status(chat_id: int) -> dict:
    now = datetime.now(timezone.utc).timestamp()
    with _lock:
        times = _windows.get(chat_id, [])
        times = [t for t in times if now - t < WINDOW_SECONDS]
        return {"commands_last_minute": len(times), "max": MAX_COMMANDS}
