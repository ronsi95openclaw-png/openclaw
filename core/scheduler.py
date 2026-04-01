"""APScheduler-based reminder system for ClawBot.

Reminders are persisted to data/tasks.json and survive bot restarts.
The scheduler is started once in receiver.py and shared globally.

Usage:
    from core.scheduler import start_scheduler, add_reminder, get_reminders, cancel_reminder
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_DATA_DIR   = Path(__file__).parent.parent / "data"
_TASKS_FILE = _DATA_DIR / "tasks.json"

_scheduler: Optional[AsyncIOScheduler] = None
_send_fn = None   # injected by receiver.py: async def send_fn(chat_id, text)


def set_send_fn(fn) -> None:
    """Inject the Telegram send function so reminders can fire alerts."""
    global _send_fn
    _send_fn = fn


def start_scheduler() -> AsyncIOScheduler:
    """Create and start the global scheduler. Call once at bot startup."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()
    _reload_from_disk()
    return _scheduler


def _load_tasks() -> List[dict]:
    if _TASKS_FILE.exists():
        try:
            return json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_tasks(tasks: List[dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def _reload_from_disk() -> None:
    """Re-schedule all persisted reminders after a restart."""
    if _scheduler is None:
        return
    for task in _load_tasks():
        if task.get("status") == "pending":
            _schedule_job(task)


def _schedule_job(task: dict) -> None:
    """Add a single task to the APScheduler."""
    if _scheduler is None:
        return
    try:
        hour, minute = task["time"].split(":")
        _scheduler.add_job(
            _fire_reminder,
            CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
            id=task["id"],
            replace_existing=True,
            kwargs={"task_id": task["id"]},
        )
    except Exception as exc:
        print(f"Failed to schedule task {task['id']}: {exc}")


async def _fire_reminder(task_id: str) -> None:
    """Called by APScheduler when a reminder fires."""
    tasks = _load_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task or task.get("status") != "pending":
        return

    if _send_fn:
        text = (
            f"⏰ <b>Reminder</b>\n\n"
            f"{task['text']}\n\n"
            f"<i>Scheduled for {task['time']} UTC</i>"
        )
        await _send_fn(task["chat_id"], text)

    # Mark as fired
    task["status"] = "fired"
    _save_tasks(tasks)

    # Remove from scheduler
    if _scheduler and _scheduler.get_job(task_id):
        _scheduler.remove_job(task_id)


def add_reminder(chat_id: int, time_str: str, text: str) -> dict:
    """Add a new reminder.

    Args:
        chat_id: Telegram chat ID to send the reminder to.
        time_str: Time in "HH:MM" format (UTC).
        text: Reminder message text.

    Returns:
        The created task dict.

    Raises:
        ValueError: If time_str is not valid "HH:MM".
    """
    # Validate time format
    parts = time_str.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid time format '{time_str}'. Use HH:MM (UTC).")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {time_str}")

    tasks = _load_tasks()
    task_id = f"reminder_{chat_id}_{int(datetime.now(timezone.utc).timestamp())}"
    task = {
        "id": task_id,
        "chat_id": chat_id,
        "time": f"{hour:02d}:{minute:02d}",
        "text": text,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    tasks.append(task)
    _save_tasks(tasks)
    _schedule_job(task)
    return task


def get_reminders(chat_id: int) -> List[dict]:
    """Return all pending reminders for a chat."""
    return [
        t for t in _load_tasks()
        if t["chat_id"] == chat_id and t["status"] == "pending"
    ]


def cancel_reminder(task_id: str) -> bool:
    """Cancel a pending reminder by ID. Returns True if found and cancelled."""
    tasks = _load_tasks()
    for task in tasks:
        if task["id"] == task_id and task["status"] == "pending":
            task["status"] = "cancelled"
            _save_tasks(tasks)
            if _scheduler and _scheduler.get_job(task_id):
                _scheduler.remove_job(task_id)
            return True
    return False
