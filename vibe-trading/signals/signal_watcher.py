"""
MNQ Signal Watcher
==================
Watches for MNQ (Micro Nasdaq Futures) trading signals and sends
Telegram alerts when signals are detected.

Usage:
    python signal_watcher.py

Requires .env at project root (two dirs up) with:
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

# ── Load .env from project root ──────────────────────────────────────────────
HERE = Path(__file__).parent                    # vibe-trading/signals/
PROJECT_ROOT = HERE.parent.parent               # Claude-openclaw/

env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL_SEC  = int(os.environ.get("SIGNAL_POLL_INTERVAL", "10"))

# Signal drop folder – other processes write .json signal files here
SIGNAL_DIR = HERE / "incoming"
SIGNAL_DIR.mkdir(exist_ok=True)

PROCESSED_DIR = HERE / "processed"
PROCESSED_DIR.mkdir(exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "signal_watcher.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("signal_watcher")


def send_telegram(message: str) -> bool:
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info("Telegram alert sent.")
            return True
        log.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        log.error(f"Telegram request failed: {e}")
        return False


def format_signal(sig: dict) -> str:
    """Format a signal dict into a Telegram message."""
    ts    = sig.get("timestamp", datetime.utcnow().isoformat())
    sym   = sig.get("symbol", "MNQ")
    side  = sig.get("side", "???").upper()
    price = sig.get("price", "?")
    tp    = sig.get("take_profit", "?")
    sl    = sig.get("stop_loss", "?")
    note  = sig.get("note", "")

    emoji = "🟢" if side == "LONG" else "🔴" if side == "SHORT" else "⚪"
    msg = (
        f"{emoji} *MNQ Signal – {side}*\n"
        f"Symbol : `{sym}`\n"
        f"Entry  : `{price}`\n"
        f"TP     : `{tp}`\n"
        f"SL     : `{sl}`\n"
        f"Time   : `{ts}`\n"
    )
    if note:
        msg += f"Note   : _{note}_\n"
    return msg


def process_signal_file(path: Path) -> None:
    """Read, alert, and archive a signal file."""
    try:
        with open(path) as f:
            sig = json.load(f)
        log.info(f"Processing signal: {path.name} → {sig}")
        msg = format_signal(sig)
        send_telegram(msg)
        # Archive
        dest = PROCESSED_DIR / f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{path.name}"
        path.rename(dest)
        log.info(f"Archived to {dest.name}")
    except Exception as e:
        log.error(f"Failed to process {path.name}: {e}")


def main():
    log.info("=" * 60)
    log.info("MNQ Signal Watcher started")
    log.info(f"Project root : {PROJECT_ROOT}")
    log.info(f"Signal dir   : {SIGNAL_DIR}")
    log.info(f"Poll interval: {POLL_INTERVAL_SEC}s")
    log.info(f"Telegram bot : {'SET' if TELEGRAM_BOT_TOKEN else 'MISSING'}")
    log.info(f"Telegram chat: {'SET' if TELEGRAM_CHAT_ID else 'MISSING'}")
    log.info("=" * 60)

    send_telegram(
        "✅ *MNQ Signal Watcher online*\n"
        f"Watching `{SIGNAL_DIR}`\n"
        f"Poll every {POLL_INTERVAL_SEC}s"
    )

    while True:
        files = sorted(SIGNAL_DIR.glob("*.json"))
        if files:
            for f in files:
                process_signal_file(f)
        else:
            log.debug("No signals found, sleeping…")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
