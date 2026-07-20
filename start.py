"""Cloud entry point — runs Telegram bot + web dashboard in one process.

Bot runs in a background thread (its own asyncio loop).
Flask dashboard runs in the main thread on $PORT (default 8080).

Usage:
    python start.py
"""
from __future__ import annotations

import os
import sys
import threading

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load .env before any project imports
from dotenv import load_dotenv
load_dotenv(override=True)

# Ensure data directories exist (important on first cloud boot)
from pathlib import Path
for _d in ["data", "data/logs"]:
    Path(_d).mkdir(parents=True, exist_ok=True)


def _run_bot() -> None:
    """Start the Telegram bot in this thread (blocking)."""
    import asyncio
    # AsyncIOScheduler (core/scheduler.py) grabs the current thread's event
    # loop on start(); a background thread has none by default, which raises.
    asyncio.set_event_loop(asyncio.new_event_loop())
    from content.receiver import main
    main()


def _run_dashboard() -> None:
    """Start Flask dashboard in this thread (blocking)."""
    from dashboard.app import app
    port = int(os.getenv("PORT", 8080))
    print(f"📊 Dashboard → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Bot in background thread. daemon=True so if the main (dashboard) thread
    # crashes, the Python process can exit cleanly instead of hanging on the
    # bot's asyncio loop. Process supervisor (Railway/systemd) will restart.
    bot_thread = threading.Thread(target=_run_bot, name="clawbot", daemon=True)
    bot_thread.start()
    print("🤖 ClawBot thread started.")

    # Dashboard in main thread (Railway / Render need a bound port)
    _run_dashboard()
