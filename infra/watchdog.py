"""
watchdog.py — Detects whether the ClawBot Telegram process is alive and, if not,
sends a Telegram alert.

Run on a schedule (e.g. Windows Task Scheduler every 5 min):
    python -m infra.watchdog

The detection kernel (`bot_is_running`) is pure and unit-tested. OS process
enumeration and the Telegram send are thin, stdlib-only wrappers so the watchdog
keeps working even if the bot's own virtualenv/packages are broken.

Alert-only by design: it does NOT auto-restart a trading process unattended.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

# The bot is launched as `python -m content.receiver`; match the common variants.
DEFAULT_MARKERS = ["content.receiver", "content\\receiver.py", "content/receiver.py"]

BOT_NAME = "ClawBot"


def bot_is_running(cmdlines: Iterable[Optional[str]], markers: Iterable[str]) -> bool:
    """True if any process command line contains any marker. Pure + testable."""
    marker_list = list(markers)
    for cl in cmdlines:
        text = cl or ""
        if any(m in text for m in marker_list):
            return True
    return False


def format_down_alert(bot_name: str, when: str) -> str:
    return (
        f"⚠️ {bot_name} appears to be DOWN as of {when}.\n"
        f"The watchdog could not find its process. "
        f"Check the machine and restart it if needed."
    )


def list_python_cmdlines() -> List[str]:
    """Command lines of running python processes (Windows, via CIM). Best-effort."""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='python.exe' OR Name='pythonw.exe'\" "
                "| Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True, text=True, timeout=20,
        )
        return [line for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def send_telegram_alert(message: str, token: str, chat_id: str) -> bool:
    """Send a plain-text Telegram message via stdlib urllib. Returns success."""
    if not token or not chat_id:
        print(f"[WATCHDOG] (telegram not configured) {message}")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": message}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:  # network/credentials issues should not crash the check
        print(f"[WATCHDOG] alert send failed: {exc}")
        return False


def _load_env() -> None:
    """Best-effort load of the repo-root .env so a scheduled run has credentials."""
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def check(markers: Optional[List[str]] = None) -> bool:
    """Check the bot; alert if down. Returns True if running."""
    _load_env()
    running = bot_is_running(list_python_cmdlines(), markers or DEFAULT_MARKERS)
    if running:
        print(f"[WATCHDOG] {BOT_NAME} is running.")
        return True
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    send_telegram_alert(
        format_down_alert(BOT_NAME, when),
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )
    return False


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
