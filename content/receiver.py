"""Telegram bot receiver for OpenClaw content pipeline.

This is the main entry point for the video workflow:

  1. You record footage on your Meta Ray-Ban glasses
  2. Save / AirDrop / share the video to your phone
  3. Open Telegram and forward the video to this bot
  4. The bot downloads it, runs the full pipeline (edit → captions → review)
  5. You get the finished reel back with /approve or /reject buttons
  6. On /approve it posts to TikTok + Instagram automatically

Commands:
  /start    — welcome message
  /status   — show pipeline status
  /approve  — approve the last reel and post to socials
  /reject   — discard the last reel

Run with:
  python -m content.receiver
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from security.whitelist import is_authorized

load_dotenv()

logger = logging.getLogger("openclaw.receiver")

# Shared state — last processed reel waiting for approval
_pending: dict = {}   # keys: reel_path, captions, chat_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_pipeline_in_background(video_path: Path, chat_id: int) -> None:
    """Run the heavy pipeline in a thread so the bot stays responsive."""
    # Import here to avoid circular imports at module load
    from content.pipeline import process
    from content.poster import post_to_socials_sync

    global _pending

    try:
        reel_path, captions = process(video_path, return_artifacts=True)
        _pending = {
            "reel_path": reel_path,
            "captions": captions,
            "chat_id": chat_id,
        }
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot Content Pipeline</b>\n\n"
        "Send me a video from your Ray-Ban glasses and I'll:\n"
        "  🎬 Edit it to 9:16 reel format\n"
        "  🎙 Add auto-captions via Whisper\n"
        "  🎵 Mix in background music\n"
        "  🧠 Write Instagram + TikTok captions with AI\n"
        "  📤 Send it back for your approval\n\n"
        "After review:\n"
        "  /approve — posts to TikTok + Instagram\n"
        "  /reject  — discards the reel\n\n"
        "Just send the video to get started!",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if _pending:
        reel = Path(_pending["reel_path"])
        await update.message.reply_text(
            f"⏳ <b>Pending approval:</b> <code>{reel.name}</code>\n"
            "Reply /approve to post or /reject to discard.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("✅ No reel pending. Send me a video!")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not _pending:
        await update.message.reply_text("No reel pending approval. Send me a video first!")
        return

    await update.message.reply_text("✅ Approved! Posting to TikTok + Instagram...")

    from content.poster import post_to_socials_sync
    reel_path = Path(_pending["reel_path"])
    captions = _pending["captions"]
    _pending.clear()

    def _post():
        try:
            results = post_to_socials_sync(reel_path, captions)
            asyncio.run(
                update.message.reply_text(
                    f"🚀 <b>Posted!</b>\n{results}",
                    parse_mode="HTML",
                )
            )
        except Exception as exc:
            asyncio.run(
                update.message.reply_text(f"🚨 Post failed: <code>{exc}</code>", parse_mode="HTML")
            )

    threading.Thread(target=_post, daemon=True).start()


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not _pending:
        await update.message.reply_text("No reel pending.")
        return

    reel_path = Path(_pending.pop("reel_path", ""))
    _pending.clear()
    if reel_path.exists():
        reel_path.unlink()
    await update.message.reply_text("🗑 Reel rejected and deleted. Send me another video!")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a video message, download it, kick off the pipeline."""
    if not is_authorized(update.effective_chat.id):
        return

    msg = update.message
    video = msg.video or msg.document

    if video is None:
        await msg.reply_text("Please send a video file.")
        return

    await msg.reply_text(
        "📥 <b>Video received!</b>\n"
        "Starting pipeline: edit → captions → music → AI copy...\n"
        "I'll send you the finished reel when it's ready.",
        parse_mode="HTML",
    )

    # Download to a temp file
    tmp_dir = Path(tempfile.mkdtemp(prefix="openclaw_"))
    suffix = ".mp4"
    if hasattr(video, "file_name") and video.file_name:
        suffix = Path(video.file_name).suffix or ".mp4"
    tmp_path = tmp_dir / f"raybان_{update.message.message_id}{suffix}"

    tg_file = await context.bot.get_file(video.file_id)
    await tg_file.download_to_drive(str(tmp_path))

    chat_id = update.effective_chat.id
    threading.Thread(
        target=_run_pipeline_in_background,
        args=(tmp_path, chat_id),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    print("🦾 ClawBot receiver is running. Send videos to your Telegram bot!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
