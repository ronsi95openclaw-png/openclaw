"""Hermes — the oversight loop.

run_check() gathers on-disk health for every overseen project, composes a
briefing, and pushes it to Telegram (best-effort, mirroring trading/executor.py
`_notify` but using HERMES_BOT_TOKEN + HERMES_CHAT_ID).

main() runs run_check() on an interval (HERMES_CHECK_INTERVAL_MINUTES, default 30)
via APScheduler — the same dependency the rest of the system already uses.

Usage:
    python -m hermes.overseer          # run the 24/7 oversight loop
    python -m hermes.overseer --once   # print one briefing and exit (no send)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load the consolidated repo-root .env before reading any HERMES_* vars.
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

from hermes.briefing import compose_briefing
from hermes.health import get_all_health

logger = logging.getLogger("hermes.overseer")

_DEFAULT_INTERVAL_MIN = 30


def _notify(message: str) -> bool:
    """Best-effort Telegram alert via the Hermes bot; never raises.

    Mirrors trading/executor.py `_notify` but uses the Hermes credentials.
    Returns True if a send was attempted and accepted, else False.
    """
    token   = os.getenv("HERMES_BOT_TOKEN", "").strip()
    chat_id = os.getenv("HERMES_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("HERMES_BOT_TOKEN / HERMES_CHAT_ID not set — skipping send.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning(f"Hermes alert send failed: {exc}")
        return False


def run_check(send: bool = True) -> str:
    """Gather health, build the briefing, optionally send it. Returns the text.

    Never raises — oversight must keep running even if a probe or send fails.
    """
    try:
        health   = get_all_health()
        briefing = compose_briefing(health)
    except Exception as exc:
        logger.exception("Health/briefing build failed: %s", exc)
        briefing = f"🪽 Hermes Briefing — error building report: {exc}"
    if send:
        _notify(briefing)
    return briefing


def _interval_minutes() -> int:
    try:
        return int(os.getenv("HERMES_CHECK_INTERVAL_MINUTES", str(_DEFAULT_INTERVAL_MIN)))
    except ValueError:
        return _DEFAULT_INTERVAL_MIN


def main() -> None:
    """Run the oversight loop. Use --once to print a single briefing and exit."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if "--once" in sys.argv:
        # Single-shot test mode: print the briefing, don't send.
        print(run_check(send=False))
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    interval = _interval_minutes()

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_check,
        "interval",
        minutes=interval,
        id="hermes_oversight",
        max_instances=1,
        coalesce=True,
    )

    logger.info("🪽 Hermes overseer running — checking every %d min.", interval)
    run_check()  # fire an immediate check on startup
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Hermes overseer stopped.")


if __name__ == "__main__":
    main()
