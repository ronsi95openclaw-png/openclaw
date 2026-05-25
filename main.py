"""OpenClaw entry point — starts the API server and trading bot together.

Railway (and any other platform) can use `python main.py` as the start
command. This module boots:
  1. uvicorn (FastAPI dashboard API) on $PORT (default 8000) — background thread
  2. CryptoComBot scan loop                                   — foreground

Both share the same process so Railway's single-service model works cleanly.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("openclaw.main")


def _start_api_server() -> None:
    """Start uvicorn in a background daemon thread."""
    import uvicorn
    from dashboard.api.server import app

    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting API server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def main() -> None:
    # ── API server (background thread) ──────────────────────────────────────
    api_thread = threading.Thread(target=_start_api_server, name="api-server", daemon=True)
    api_thread.start()

    # Give uvicorn a moment to bind before the bot floods logs
    time.sleep(3)

    # ── Trading bot (foreground) ─────────────────────────────────────────────
    from trading.cryptocom_bot import get_bot

    bot = get_bot()
    bot.start()
    logger.info("OpenClaw bot started — scanning every %ds", bot.state.scan_interval)

    # Keep the main thread alive; bot scan loop runs in its own thread
    try:
        while True:
            time.sleep(60)
            if not bot.is_running():
                logger.warning("Bot stopped unexpectedly — restarting")
                bot.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down")
        bot.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
