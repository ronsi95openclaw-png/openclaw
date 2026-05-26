"""Telegram outbox relay — runs on the local machine.

Railway's IP is blocked by Telegram's Bot API allowlist so it cannot call
api.telegram.org/sendMessage directly.  Instead, Railway writes replies to the
Supabase telegram_outbox table.  This daemon runs locally (where the IP IS
allowed), polls that table every 3 seconds, sends each pending message via
Telegram, then marks it sent.

Start automatically via main.py when RAILWAY_PUBLIC_URL is not set (local mode).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("openclaw.runtime.telegram_relay")

_POLL_INTERVAL = 3    # seconds between Supabase polls
_MAX_AGE_SEC   = 120  # discard messages older than 2 minutes (avoid stale replies)


def _send_direct(token: str, chat_id: str, text: str,
                 parse_mode: str = "HTML") -> bool:
    """Send a Telegram message directly from the local machine."""
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": parse_mode,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode())
        return bool(result.get("ok"))
    except Exception as exc:
        logger.debug("sendMessage failed: %s", exc)
        return False


class TelegramRelayDaemon:
    """Polls Supabase telegram_outbox and forwards pending messages to Telegram."""

    def __init__(self) -> None:
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="telegram-relay", daemon=True
        )
        self._thread.start()
        logger.info("TelegramRelayDaemon started — polling Supabase outbox every %ds",
                    _POLL_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TelegramRelayDaemon stopped")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._flush()
            except Exception as exc:
                logger.debug("Relay flush error: %s", exc)
            self._stop.wait(_POLL_INTERVAL)

    def _flush(self) -> None:
        from infra.supabase_client import get_client
        sb = get_client()
        if sb is None:
            return

        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return

        # Fetch unsent messages ordered oldest-first
        res = sb.table("telegram_outbox") \
                .select("id, chat_id, text, parse_mode, created_at") \
                .is_("sent_at", "null") \
                .order("created_at") \
                .limit(20) \
                .execute()

        if not res.data:
            return

        from datetime import datetime, timezone, timedelta
        now      = datetime.now(timezone.utc)
        cutoff   = now - timedelta(seconds=_MAX_AGE_SEC)

        for row in res.data:
            msg_id     = row["id"]
            created_at = row.get("created_at", "")

            # Parse timestamp and discard if too old
            try:
                ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if ts < cutoff:
                    _mark_sent(sb, msg_id, error="discarded — too old")
                    continue
            except Exception:
                pass

            ok = _send_direct(
                token,
                str(row["chat_id"]),
                str(row["text"]),
                str(row.get("parse_mode", "HTML")),
            )
            if ok:
                _mark_sent(sb, msg_id)
                logger.debug("Relay: sent message %s to chat %s", msg_id, row["chat_id"])
            else:
                _mark_sent(sb, msg_id, error="sendMessage failed — see local logs")


def _mark_sent(sb, msg_id: str, error: Optional[str] = None) -> None:
    from datetime import datetime, timezone
    update = {"sent_at": datetime.now(timezone.utc).isoformat()}
    if error:
        update["error"] = error
    try:
        sb.table("telegram_outbox").update(update).eq("id", msg_id).execute()
    except Exception as exc:
        logger.debug("mark_sent failed: %s", exc)


# ── Singleton ──────────────────────────────────────────────────────────────────

_relay: Optional[TelegramRelayDaemon] = None


def get_relay() -> TelegramRelayDaemon:
    global _relay
    if _relay is None:
        _relay = TelegramRelayDaemon()
    return _relay
