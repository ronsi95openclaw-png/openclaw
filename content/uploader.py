"""Telegram-based approval uploader for OpenClaw content pipeline.

Sends the finished reel + captions to Telegram for manual review.
The operator replies /approve or /reject to control release.

This module handles the outbound send only. Approval handling (polling
for the operator reply) lives in pipeline.py.

Sync wrappers use requests directly (not asyncio.run) so they are safe
to call from both sync and async contexts, including the Telegram receiver
event loop.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import requests
from telegram import Bot
from telegram.error import TelegramError

from content.caption_generator import Captions

_TELEGRAM_BASE = "https://api.telegram.org/bot{token}/{method}"


def _bot_post(token: str, method: str, timeout: int = 10, **kwargs) -> dict:
    """Raw synchronous Telegram Bot API call via requests."""
    url = _TELEGRAM_BASE.format(token=token, method=method)
    r = requests.post(url, timeout=timeout, **kwargs)
    r.raise_for_status()
    return r.json()


async def send_for_approval(video_path: Path, captions: Captions) -> int:
    """Send the reel video and captions to Telegram for approval.

    Args:
        video_path: Path to the finished reel MP4.
        captions: Generated captions dataclass.

    Returns:
        The Telegram message_id of the sent video (used to track approval).

    Raises:
        TelegramError: If the Telegram API call fails.
        FileNotFoundError: If video_path does not exist.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Reel not found: {video_path}")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("  ⚠️  Telegram not configured — skipping approval send.")
        return -1

    caption_text = (
        "🎬 <b>New Reel Ready for Approval</b>\n\n"
        f"<b>Instagram:</b>\n{captions.instagram[:800]}\n\n"
        f"<b>TikTok:</b>\n{captions.tiktok[:300]}\n\n"
        "Reply <b>/approve</b> to mark as approved or <b>/reject</b> to discard."
    )

    bot = Bot(token=token)
    try:
        with open(video_path, "rb") as f:
            msg = await bot.send_video(
                chat_id=chat_id,
                video=f,
                caption=caption_text,
                parse_mode="HTML",
                supports_streaming=True,
            )
        print(f"  📤 Reel sent to Telegram for approval (message_id={msg.message_id})")
        return msg.message_id
    except TelegramError as exc:
        raise TelegramError(f"Failed to send reel for approval: {exc}") from exc


def send_for_approval_sync(video_path: Path, captions: Captions) -> int:
    """Send a reel for approval without requiring a running event loop.

    Uses requests directly to avoid asyncio.run() conflicts when called
    from the Telegram bot's async event loop via a thread or sync context.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Reel not found: {video_path}")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("  ⚠️  Telegram not configured — skipping approval send.")
        return -1

    caption_text = (
        "🎬 <b>New Reel Ready for Approval</b>\n\n"
        f"<b>Instagram:</b>\n{captions.instagram[:800]}\n\n"
        f"<b>TikTok:</b>\n{captions.tiktok[:300]}\n\n"
        "Reply <b>/approve</b> to mark as approved or <b>/reject</b> to discard."
    )

    try:
        with open(video_path, "rb") as f:
            data = _bot_post(
                token, "sendVideo", timeout=120,
                data={"chat_id": chat_id, "caption": caption_text,
                      "parse_mode": "HTML", "supports_streaming": "true"},
                files={"video": f},
            )
        msg_id = data.get("result", {}).get("message_id", -1)
        print(f"  📤 Reel sent to Telegram for approval (message_id={msg_id})")
        return msg_id
    except Exception as exc:
        raise TelegramError(f"Failed to send reel for approval: {exc}") from exc


async def send_status(message: str) -> None:
    """Send a plain status message to the Telegram chat (async)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
    except TelegramError:
        pass  # Status updates are best-effort


def send_status_sync(message: str) -> None:
    """Send a status message without requiring a running event loop.

    Safe to call from any context — inside or outside an asyncio event loop.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        _bot_post(
            token, "sendMessage", timeout=10,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        )
    except Exception:
        pass  # Status updates are best-effort
