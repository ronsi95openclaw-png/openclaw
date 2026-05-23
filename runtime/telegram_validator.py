"""Telegram alert system validator for OpenClaw.

Sends a synchronous test message to the configured Telegram chat and
verifies the HTTP response. Safe to run in DEMO_MODE.

Reads from environment:
    TELEGRAM_BOT_TOKEN — bot token from @BotFather
    TELEGRAM_CHAT_ID   — target chat ID

Usage:
    from runtime.telegram_validator import validate_telegram
    result = validate_telegram()
    print(result.configured, result.message_sent, result.latency_ms)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("openclaw.runtime.telegram_validator")


@dataclass
class TelegramValidationResult:
    configured: bool          # TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set
    message_sent: bool        # HTTP request succeeded and status==200
    response_status: Optional[int]   # HTTP status code, or None if not reached
    response_ok: bool         # True if API response ok==True
    latency_ms: float         # round-trip time in ms
    error: Optional[str]      # exception message if any, else None
    token_prefix: str         # first 8 chars of token (for validation log), empty if not set


def check_telegram_config() -> dict:
    """Return config status dict without sending any message.

    Returns:
        {
            "configured": bool,
            "token_set": bool,
            "chat_id_set": bool,
            "token_prefix": str,  # first 8 chars or ""
        }
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    token_set = bool(token)
    chat_id_set = bool(chat_id)
    return {
        "configured": token_set and chat_id_set,
        "token_set": token_set,
        "chat_id_set": chat_id_set,
        "token_prefix": token[:8] if token else "",
    }


def validate_telegram(
    timeout_s: float = 10.0,
    test_message: Optional[str] = None,
) -> TelegramValidationResult:
    """Send a test message synchronously and return validation result.

    If not configured (no token/chat_id): returns configured=False, message_sent=False.
    If configured: sends message via urllib (same as telegram_alerts.py), measures latency.

    The test_message defaults to:
    "✅ OpenClaw Telegram validation test — {ISO timestamp}"
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    # Read at call time (not module load time) so tests can monkeypatch env
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    token_prefix = token[:8] if token else ""

    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping validation send")
        return TelegramValidationResult(
            configured=False,
            message_sent=False,
            response_status=None,
            response_ok=False,
            latency_ms=0.0,
            error=None,
            token_prefix=token_prefix,
        )

    if test_message is None:
        ts = datetime.now(timezone.utc).isoformat()
        test_message = f"✅ OpenClaw Telegram validation test — {ts}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": test_message, "parse_mode": "HTML"}
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    response_status: Optional[int] = None
    response_ok = False
    error: Optional[str] = None
    message_sent = False

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            response_status = resp.status
            raw = resp.read()
            try:
                body = json.loads(raw)
                response_ok = bool(body.get("ok", False))
            except Exception:
                response_ok = False
            message_sent = response_status == 200
    except urllib.error.URLError as exc:
        error = str(exc)
        logger.debug("Telegram validation URLError: %s", exc)
    except Exception as exc:
        error = str(exc)
        logger.debug("Telegram validation error: %s", exc)
    finally:
        latency_ms = (time.monotonic() - t0) * 1000.0

    logger.info(
        "Telegram validation complete  configured=True  sent=%s  status=%s  "
        "ok=%s  latency_ms=%.1f  token_prefix=%s",
        message_sent,
        response_status,
        response_ok,
        latency_ms,
        token_prefix,
    )

    return TelegramValidationResult(
        configured=True,
        message_sent=message_sent,
        response_status=response_status,
        response_ok=response_ok,
        latency_ms=latency_ms,
        error=error,
        token_prefix=token_prefix,
    )
