"""APScheduler-based reminder + auto-trade system for ClawBot.

Reminders are persisted to data/tasks.json and survive bot restarts.
Auto-trade job runs daily at 08:00 UTC when enabled.
The scheduler is started once in receiver.py and shared globally.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_DATA_DIR        = Path(__file__).parent.parent / "data"
_TASKS_FILE      = _DATA_DIR / "tasks.json"
_AUTOTRADE_FILE  = _DATA_DIR / "autotrade.json"
_AUTOTRADE_JOB   = "clawbot_autotrade_daily"

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
    task_id = f"reminder_{chat_id}_{time.time_ns()}"
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


# ---------------------------------------------------------------------------
# Auto-trade daily job
# ---------------------------------------------------------------------------

def _load_autotrade() -> dict:
    if _AUTOTRADE_FILE.exists():
        try:
            return json.loads(_AUTOTRADE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False, "chat_id": None, "scan_time": "08:00", "timeframe": "4h"}


def _save_autotrade(cfg: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _AUTOTRADE_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


async def _run_autotrade() -> None:
    """Daily auto-trade job: scan → execute HIGH signals → notify."""
    cfg = _load_autotrade()
    if not cfg.get("enabled") or not cfg.get("chat_id"):
        return

    chat_id   = cfg["chat_id"]
    timeframe = cfg.get("timeframe", "4h")

    if _send_fn:
        await _send_fn(chat_id, "🤖 <b>Auto-Trade Scan running...</b>")

    try:
        from trading.exchange import fetch_all_closes, get_account_balance, get_portfolio_value_usd
        from trading.strategy import RSIMACDStrategy
        from trading.executor import execute_signals

        strategy     = RSIMACDStrategy()
        candle_data  = fetch_all_closes(strategy.config.coins, timeframe=timeframe, count=100)
        signals      = strategy.scan_all(candle_data)
        high_signals = [s for s in signals if s.action != "HOLD" and s.confidence == "HIGH"]

        if not high_signals:
            # Show current RSI status even if no signals
            from trading.strategy import calculate_rsi, calculate_macd
            lines = [f"📊 <b>Daily Scan — {timeframe}</b>\n<i>No HIGH confidence signals</i>\n"]
            for coin, closes in candle_data.items():
                try:
                    rsi        = calculate_rsi(closes)
                    _, _, hist = calculate_macd(closes)
                    trend      = "↑" if hist > 0 else "↓"
                    lines.append(f"⚪ {coin}: RSI <code>{rsi:.1f}</code> {trend}")
                except Exception:
                    pass
            if _send_fn:
                await _send_fn(chat_id, "\n".join(lines))
            return

        # Get portfolio value for position sizing
        try:
            balances      = get_account_balance()
            portfolio_usd = get_portfolio_value_usd(balances)
        except Exception:
            portfolio_usd = 1000.0  # fallback if balance fetch fails

        results = execute_signals(high_signals, portfolio_usd)

        # Build Telegram notification
        ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"🤖 <b>Auto-Trade Report — {ts}</b>\n"]

        for signal, result in zip(high_signals, results):
            status = result.get("status", "unknown")
            if status == "executed":
                action_emoji = "🟢" if signal.action == "BUY" else "🔴"
                lines.append(
                    f"{action_emoji} <b>{signal.action} {signal.coin}</b>\n"
                    f"   Amount: <code>${result.get('usd_amount', 0):.2f}</code>\n"
                    f"   Price:  <code>${result.get('price', 0):,.2f}</code>\n"
                    f"   RSI:    <code>{signal.rsi:.1f}</code>\n"
                    f"   Order:  <code>{result.get('order_id', 'N/A')}</code>"
                )
            elif status == "skipped":
                lines.append(f"⚪ Skipped {signal.coin}: {result.get('reason', '')}")
            else:
                lines.append(f"🚨 Error {signal.coin}: {result.get('reason', 'unknown error')}")

        lines.append(f"\n<i>Portfolio: ~${portfolio_usd:,.0f} | Risk: 1.5% per trade</i>")

        if _send_fn:
            await _send_fn(chat_id, "\n".join(lines))

    except Exception as exc:
        if _send_fn:
            await _send_fn(chat_id, f"🚨 <b>Auto-Trade error:</b> <code>{exc}</code>")


def enable_autotrade(chat_id: int, scan_time: str = "08:00", timeframe: str = "4h") -> dict:
    """Enable daily auto-trade. Returns the config."""
    cfg = {
        "enabled":   True,
        "chat_id":   chat_id,
        "scan_time": scan_time,
        "timeframe": timeframe,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_autotrade(cfg)

    if _scheduler:
        hour, minute = scan_time.split(":")
        _scheduler.add_job(
            _run_autotrade,
            CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
            id=_AUTOTRADE_JOB,
            replace_existing=True,
        )
    return cfg


def disable_autotrade() -> None:
    """Disable the daily auto-trade job."""
    cfg = _load_autotrade()
    cfg["enabled"] = False
    _save_autotrade(cfg)
    if _scheduler and _scheduler.get_job(_AUTOTRADE_JOB):
        _scheduler.remove_job(_AUTOTRADE_JOB)


def get_autotrade_status() -> dict:
    """Return current auto-trade config."""
    return _load_autotrade()


async def run_autotrade_now() -> None:
    """Public entry point to trigger the auto-trade scan immediately."""
    await _run_autotrade()


def reload_autotrade() -> None:
    """Re-register auto-trade job after restart if it was enabled."""
    cfg = _load_autotrade()
    if cfg.get("enabled") and cfg.get("chat_id") and _scheduler:
        scan_time = cfg.get("scan_time", "08:00")
        hour, minute = scan_time.split(":")
        _scheduler.add_job(
            _run_autotrade,
            CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
            id=_AUTOTRADE_JOB,
            replace_existing=True,
        )
