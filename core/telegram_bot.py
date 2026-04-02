"""Telegram notification support for OpenClaw.

Provides send_alert() for pushing formatted trade alerts to a Telegram chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.

Usage:
    import asyncio
    from core.telegram_bot import send_alert, AlertType
    asyncio.run(send_alert(AlertType.DCA_EXECUTED, asset="BTC", action="BUY", reasoning="RSI 28"))
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError


class AlertType(str, Enum):
    TRADE_SIGNAL = "TRADE_SIGNAL"
    DCA_EXECUTED = "DCA_EXECUTED"
    FUTURES_SIGNAL = "FUTURES_SIGNAL"
    ERROR = "ERROR"
    PORTFOLIO_UPDATE = "PORTFOLIO_UPDATE"


_ALERT_EMOJI = {
    AlertType.TRADE_SIGNAL: "📊",
    AlertType.DCA_EXECUTED: "✅",
    AlertType.FUTURES_SIGNAL: "📉",
    AlertType.ERROR: "🚨",
    AlertType.PORTFOLIO_UPDATE: "💼",
}


def _format_message(
    alert_type: AlertType,
    asset: Optional[str] = None,
    action: Optional[str] = None,
    reasoning: Optional[str] = None,
    message: Optional[str] = None,
) -> str:
    type_emoji = _ALERT_EMOJI.get(alert_type, "📊")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "🦾 <b>CLAWBOT ALERT</b>",
        f"{type_emoji} <b>Type:</b> {alert_type.value}",
    ]
    if asset:
        lines.append(f"💰 <b>Asset:</b> {asset}")
    if action:
        lines.append(f"📈 <b>Action:</b> {action}")
    if reasoning:
        lines.append(f"🧠 <b>Reasoning:</b> {reasoning}")
    if message:
        lines.append(f"📝 <b>Message:</b> {message}")
    lines.append(f"⏰ <b>Time:</b> {timestamp}")

    return "\n".join(lines)


async def send_alert(
    alert_type: AlertType,
    asset: Optional[str] = None,
    action: Optional[str] = None,
    reasoning: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """Send a formatted alert to the configured Telegram chat.

    Silently no-ops if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are unset,
    so the bot still works without Telegram configured.

    Args:
        alert_type: Category of alert (see AlertType enum).
        asset: Trading pair or asset symbol, e.g. "BTC".
        action: Trade action, e.g. "BUY" / "SELL" / "HOLD".
        reasoning: LLM reasoning text.
        message: Free-form message override (used for ERROR / PORTFOLIO_UPDATE).

    Raises:
        TelegramError: If the Telegram API call fails.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        return

    text = _format_message(alert_type, asset=asset, action=action, reasoning=reasoning, message=message)

    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except TelegramError as exc:
        raise TelegramError(f"Failed to send Telegram alert: {exc}") from exc


def send_alert_sync(
    alert_type: AlertType,
    asset: Optional[str] = None,
    action: Optional[str] = None,
    reasoning: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    """Synchronous wrapper around send_alert() for use in non-async code."""
    asyncio.run(send_alert(alert_type, asset=asset, action=action, reasoning=reasoning, message=message))
