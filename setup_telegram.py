#!/usr/bin/env python3
"""
Telegram setup / verify for the HERMES bot.

WHY THIS SCRIPT IS THE WAY IT IS  (see hermes/SETUP_CHECKLIST.md):
  - ClawBot is the LIVE-TRADING bot (content/receiver.py). It reads the
    project-root .env key TELEGRAM_BOT_TOKEN. This script must NEVER touch
    that file -- doing so shadows ClawBot's token and makes the two bots
    fight over one Telegram poll (HTTP 409 conflict).
  - Hermes is a SEPARATE gateway with its OWN config:
        %LOCALAPPDATA%\\hermes\\.env       (key: TELEGRAM_BOT_TOKEN)
    This script reads/writes ONLY that file.

WHAT IT DOES (safe + idempotent):
  1. Reads the Hermes token from the Hermes .env (or prompts, if a terminal).
  2. Verifies it with getMe.
  3. Resolves the chat id from TELEGRAM_HOME_CHANNEL / TELEGRAM_ALLOWED_USERS,
     else getUpdates, else a prompt.
  4. Writes back ONLY values that are new/changed -- surgically, preserving
     every other line, all comments, and the file's CRLF line endings.
  5. Sends a test message.

It never rewrites the project-root .env, never flattens a config, and never
hangs: prompts are skipped automatically when stdin is not a terminal.

Official alternative for wiring the token: `hermes gateway setup`.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
HERMES_ENV = Path(_LOCALAPPDATA) / "hermes" / ".env"

TOKEN_KEY = "TELEGRAM_BOT_TOKEN"
CHAT_KEY = "TELEGRAM_HOME_CHANNEL"


def _prompt(msg):
    """input() only when attached to a real terminal; otherwise return ''."""
    if sys.stdin and sys.stdin.isatty():
        try:
            return input(msg).strip()
        except (EOFError, KeyboardInterrupt):
            return ""
    print(msg + "  [skipped: non-interactive shell]")
    return ""


def read_env(path):
    """Parse KEY=value pairs (ignoring comments/blank lines)."""
    data = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                data[k.strip()] = v.strip().strip('"\'')
    return data


def set_env_keys(path, updates):
    """Surgically update KEY=value lines, preserving comments, order, and the
    file's existing newline style. Appends keys that don't exist. Refuses to
    proceed if the file can't be decoded cleanly (so we never corrupt it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            print(f"   ERR {path} is not clean UTF-8; refusing to rewrite it.")
            return False
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
    else:
        newline = "\r\n" if os.name == "nt" else "\n"
        lines = []

    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")

    path.write_text(newline.join(out) + newline, encoding="utf-8")
    return True


def tg(token, method, params=None):
    """Call the Telegram Bot API; always returns a dict with an 'ok' key."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if params:
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(url, data=data)
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def verify_token(token):
    r = tg(token, "getMe")
    if r.get("ok"):
        b = r["result"]
        print(f"   OK  bot verified: @{b.get('username')} ({b.get('first_name')})")
        return True
    print(f"   ERR token invalid: {r.get('description')}")
    return False


def resolve_chat_id(token, env):
    """From explicit config first, then getUpdates, then a prompt."""
    for k in (CHAT_KEY, "TELEGRAM_ALLOWED_USERS", "TELEGRAM_CHAT_ID"):
        v = env.get(k, "").split(",")[0].strip()
        if v:
            print(f"   OK  chat id from {k}: {v}")
            return v

    r = tg(token, "getUpdates", {"limit": 100, "timeout": 0})
    ups = r.get("result", []) if r.get("ok") else []
    if ups:
        msg = ups[-1].get("message") or ups[-1].get("channel_post") or {}
        cid = msg.get("chat", {}).get("id")
        if cid:
            print(f"   OK  chat id from getUpdates: {cid}")
            return str(cid)

    print("   --  no chat id on record. Send the bot a message first, then re-run.")
    return _prompt("   Enter chat id (or press Enter to skip): ")


def send_test(token, chat_id):
    r = tg(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "Hermes Telegram verified - notifications are working. (Ronsi95 AI OS)",
    })
    if r.get("ok"):
        print("   OK  test message sent. Check your Telegram.")
        return True
    print(f"   ERR send failed: {r.get('description')}")
    return False


def main():
    print("=" * 60)
    print("  HERMES TELEGRAM SETUP / VERIFY")
    print(f"  target: {HERMES_ENV}")
    print("=" * 60)

    env = read_env(HERMES_ENV)
    token = env.get(TOKEN_KEY, "").strip()
    token_is_new = False
    if token:
        print("\n[1] Token found in Hermes config.")
    else:
        print(f"\n[1] No {TOKEN_KEY} in Hermes config.")
        token = _prompt("    Paste the Hermes bot token (from @BotFather): ")
        if not token:
            print("    No token provided -- nothing to do.")
            print("    (Tip: the official way to wire it is `hermes gateway setup`.)")
            return 1
        token_is_new = True

    print("\n[2] Verifying token with Telegram...")
    if not verify_token(token):
        return 1

    print("\n[3] Resolving chat id...")
    chat_id = resolve_chat_id(token, env)
    chat_is_new = bool(chat_id) and chat_id != env.get(CHAT_KEY, "").strip()

    print("\n[4] Persisting (only what changed)...")
    updates = {}
    if token_is_new:
        updates[TOKEN_KEY] = token
    if chat_id and chat_is_new:
        updates[CHAT_KEY] = chat_id
    if updates:
        if set_env_keys(HERMES_ENV, updates):
            print(f"    wrote {', '.join(updates)} to Hermes .env "
                  "(comments preserved). Restart the gateway to apply.")
    else:
        print("    nothing to write -- Hermes is already configured.")

    if chat_id:
        print("\n[5] Sending test message...")
        send_test(token, chat_id)
    else:
        print("\n[5] Skipped test message (no chat id).")

    print("\n" + "=" * 60)
    print("  DONE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
