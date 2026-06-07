"""
watchdog.py — Detects whether the ClawBot Telegram process is alive and, if not,
sends a Telegram alert.

Run on a schedule (e.g. Windows Task Scheduler every 5 min):
    python -m infra.watchdog

The detection kernel (`bot_is_running`) and the state-machine kernel (`transition`)
are pure and unit-tested. OS process enumeration, state persistence, and the
Telegram send are thin, stdlib-only wrappers so the watchdog keeps working even
if the bot's own virtualenv/packages are broken.

Alert-only by design: it does NOT auto-restart a trading process unattended.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# The bot is launched as `python -m content.receiver`; match the common variants.
# NOTE: the first marker is the exact module-launch flag (`-m content.receiver`)
# rather than the bare substring "content.receiver". This prevents false positives
# from verifier scripts launched as `python -c "...content.receiver..."`.
DEFAULT_MARKERS = ["-m content.receiver", "content\\receiver.py", "content/receiver.py"]

BOT_NAME = "ClawBot"

STATE_FILE = Path(__file__).resolve().parent / "watchdog_state.json"
DEFAULT_COOLDOWN_MINUTES = 60


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


def format_escalation_alert(bot_name: str, down_for_minutes: int) -> str:
    return (
        f"🚨 ESCALATION: {bot_name} is STILL DOWN after {down_for_minutes} minutes.\n"
        f"The watchdog has been unable to find its process across the cooldown window. "
        f"Manual intervention required."
    )


def format_recovery_alert(bot_name: str, down_for_minutes: int) -> str:
    return (
        f"✅ {bot_name} has RECOVERED — process is alive again "
        f"after roughly {down_for_minutes} minutes of downtime."
    )


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def transition(
    running: bool,
    state: dict,
    now: datetime,
    cooldown_minutes: int,
) -> Tuple[dict, Optional[str]]:
    """Pure state-machine kernel for the watchdog.

    Inputs:
        running: whether the bot was just detected as alive
        state: previous state dict — either {} (healthy) or
               {"down_since": iso, "alert_sent_at": iso}
        now: current time
        cooldown_minutes: minutes to wait between repeat alerts while still down

    Returns: (new_state, alert_kind)
        alert_kind is one of "down", "escalation", "recovery", or None.
    """
    down_since = _parse_iso(state.get("down_since"))
    alert_sent_at = _parse_iso(state.get("alert_sent_at"))

    if running:
        if down_since is None:
            # Healthy and was healthy — nothing to do.
            return {}, None
        # Recovery: was down, now back up.
        return {}, "recovery"

    # Bot is currently down.
    if down_since is None:
        # First time we noticed: emit "down", record both timestamps.
        new_state = {
            "down_since": now.isoformat(),
            "alert_sent_at": now.isoformat(),
        }
        return new_state, "down"

    # Still down — decide between cooldown silence and escalation.
    if alert_sent_at is None:
        # Defensive: if alert_sent_at is missing, treat as cooldown-elapsed
        # so we escalate rather than swallow downtime.
        new_state = {
            "down_since": state.get("down_since") or now.isoformat(),
            "alert_sent_at": now.isoformat(),
        }
        return new_state, "escalation"

    if now - alert_sent_at >= timedelta(minutes=cooldown_minutes):
        new_state = {
            "down_since": state.get("down_since") or now.isoformat(),
            "alert_sent_at": now.isoformat(),
        }
        return new_state, "escalation"

    # In cooldown, still down — preserve state, no alert.
    return dict(state), None


def load_state(path: Path = STATE_FILE) -> dict:
    """Load the persisted watchdog state; return {} if missing or unreadable."""
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    """Persist the watchdog state. Best-effort; failures are logged, not raised."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
    except Exception as exc:
        print(f"[WATCHDOG] could not persist state: {exc}")


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


def _cooldown_minutes() -> int:
    raw = os.getenv("WATCHDOG_COOLDOWN_MINUTES", "").strip()
    if not raw:
        return DEFAULT_COOLDOWN_MINUTES
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_COOLDOWN_MINUTES
    except ValueError:
        return DEFAULT_COOLDOWN_MINUTES


def _downtime_minutes(state: dict, now: datetime) -> int:
    down_since = _parse_iso(state.get("down_since"))
    if down_since is None:
        return 0
    delta = now - down_since
    return max(0, int(delta.total_seconds() // 60))


def check(markers: Optional[List[str]] = None) -> bool:
    """Check the bot; alert if down (respecting cooldown/escalation). Returns True if running."""
    _load_env()
    state = load_state()
    now = datetime.now()
    running = bot_is_running(list_python_cmdlines(), markers or DEFAULT_MARKERS)
    new_state, alert_kind = transition(running, state, now, _cooldown_minutes())

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if alert_kind == "down":
        when = now.strftime("%Y-%m-%d %H:%M")
        send_telegram_alert(format_down_alert(BOT_NAME, when), token, chat_id)
    elif alert_kind == "escalation":
        send_telegram_alert(
            format_escalation_alert(BOT_NAME, _downtime_minutes(new_state, now)),
            token,
            chat_id,
        )
    elif alert_kind == "recovery":
        # Compute downtime from the prior state (new_state is now cleared).
        send_telegram_alert(
            format_recovery_alert(BOT_NAME, _downtime_minutes(state, now)),
            token,
            chat_id,
        )
    else:
        if running:
            print(f"[WATCHDOG] {BOT_NAME} is running.")
        else:
            print(f"[WATCHDOG] {BOT_NAME} still down (cooldown active).")

    save_state(new_state)
    return running


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
