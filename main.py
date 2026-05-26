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
import signal
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


def _tg_alert(msg: str) -> None:
    """Last-ditch Telegram alert using raw requests — no bot framework dependency."""
    try:
        import requests as _req
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "6082698835")
        if not token:
            return
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass  # never crash because of an alert


def _start_api_server() -> None:
    """Start uvicorn in a background daemon thread."""
    import uvicorn
    from dashboard.api.server import app

    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting API server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def main() -> None:
    # ── SIGTERM handler (Railway sends this on deploy/restart) ───────────────
    def _handle_sigterm(signum, frame):
        logger.info("SIGTERM received — shutting down gracefully")
        _tg_alert("🛑 <b>OpenClaw shutdown</b> — SIGTERM received (Railway deploy/restart)")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # ── API server (background thread) ──────────────────────────────────────
    api_thread = threading.Thread(target=_start_api_server, name="api-server", daemon=True)
    api_thread.start()

    # Give uvicorn a moment to bind before the bot floods logs
    time.sleep(3)

    # ── Startup integrity check ──────────────────────────────────────────────
    try:
        from infra.state_store import startup_integrity_check
        integrity = startup_integrity_check()
        if not integrity["ok"]:
            logger.warning("Startup integrity check FAILED: %s", integrity["issues"])
            try:
                from runtime.telegram_alerts import _send
                _send(
                    "⚠️ <b>STARTUP STATE DRIFT DETECTED</b>\n\n"
                    + "\n".join(integrity["issues"])
                    + "\n\nBot started with execution paused. Send /resume after verification."
                )
            except Exception:
                pass
        else:
            logger.info(
                "Startup integrity OK — local=%d  supabase=%d",
                integrity["local_count"], integrity["supabase_count"],
            )
    except Exception as exc:
        logger.warning("Startup integrity check unavailable: %s", exc)
        integrity = {"ok": True}  # non-fatal — proceed

    # ── Trading bot (foreground) ─────────────────────────────────────────────
    try:
        from trading.cryptocom_bot import CryptoComBot
        bot = CryptoComBot()
        if not integrity.get("ok", True):
            bot.state.execution_paused = True
        bot.start()
        logger.info("OpenClaw bot started — scanning every %ds", bot.state.scan_interval)
    except Exception as exc:
        logger.critical("FATAL: CryptoComBot failed to start: %s", exc, exc_info=True)
        _tg_alert(
            f"🚨 <b>OPENCLAW CRASH — BOT FAILED TO START</b>\n\n"
            f"<code>{type(exc).__name__}: {exc}</code>\n\n"
            f"Railway will restart. Check deploy logs."
        )
        raise  # let Railway see the non-zero exit and restart

    # Keep the main thread alive; bot scan loop runs in its own thread
    try:
        while True:
            time.sleep(60)
            if not bot.is_running():
                logger.warning("Bot stopped unexpectedly — restarting")
                try:
                    bot.start()
                except Exception as exc:
                    logger.critical("Bot restart failed: %s", exc, exc_info=True)
                    _tg_alert(
                        f"🚨 <b>OPENCLAW BOT RESTART FAILED</b>\n"
                        f"<code>{type(exc).__name__}: {exc}</code>"
                    )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down")
        bot.stop()
        sys.exit(0)
    except Exception as exc:
        logger.critical("FATAL: main loop crashed: %s", exc, exc_info=True)
        _tg_alert(
            f"🚨 <b>OPENCLAW MAIN LOOP CRASH</b>\n\n"
            f"<code>{type(exc).__name__}: {exc}</code>\n\nRailway will restart."
        )
        raise


if __name__ == "__main__":
    main()
