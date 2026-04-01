"""Telegram bot receiver for OpenClaw content pipeline.

This is the main entry point for the video workflow:

  1. You record footage on your Meta Ray-Ban glasses
  2. Save / share the video to your phone
  3. Open Telegram and forward the video to this bot
  4. The bot downloads it, runs the full pipeline (edit → captions → review)
  5. You get the finished reel back for approval
  6. On /approve it posts to TikTok + Instagram automatically

Commands:
  /start    — welcome message + command list
  /status   — bot health, Ollama ping, last trade decision
  /trades   — last 10 trade decisions from trades.log
  /pipeline — content pipeline status (pending reel if any)
  /approve  — approve the pending reel and post to socials
  /reject   — discard the pending reel
  /stop     — graceful shutdown

Run with:
  python -m content.receiver
"""
from __future__ import annotations

import logging
import os
import signal
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
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

# ---------------------------------------------------------------------------
# Thread-safe pending state
# ---------------------------------------------------------------------------
_pending_lock = threading.Lock()
_pending: dict = {}  # keys: reel_path, captions, chat_id

_app: Application | None = None  # set in main(), used by /stop


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _read_last_trades(n: int = 10) -> list[str]:
    """Return the last N lines from trades.log that contain TRADE_DECISION."""
    log_file = Path(__file__).parent.parent / "data" / "logs" / "trades.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return [l for l in lines if "TRADE_DECISION" in l][-n:]
    except Exception:
        return []


def _ping_ollama() -> str:
    """Return 'online' or an error string."""
    try:
        from ollama import chat
        chat(model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
             messages=[{"role": "user", "content": "ping"}])
        return "online ✅"
    except Exception as exc:
        return f"offline ❌ ({exc})"


def _run_pipeline_in_background(
    video_path: Path,
    chat_id: int,
    app: Application,
) -> None:
    """Run the heavy pipeline in a thread and send the reel back when done."""
    from content.pipeline import process
    from content.uploader import send_for_approval_sync

    try:
        result = process(video_path, return_artifacts=True)
        if result is None:
            return
        reel_path, captions = result

        with _pending_lock:
            _pending.clear()
            _pending.update({
                "reel_path": str(reel_path),
                "captions": captions,
                "chat_id": chat_id,
            })

        send_for_approval_sync(reel_path, captions)

    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
        import asyncio
        asyncio.run(
            app.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 <b>Pipeline failed</b>\n<code>{exc}</code>",
                parse_mode="HTML",
            )
        )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot is online</b>\n\n"
        "<b>Content pipeline commands:</b>\n"
        "  Send a video → auto-edit + AI captions\n"
        "  /pipeline  — pipeline status\n"
        "  /approve   — post reel to TikTok + Instagram\n"
        "  /reject    — discard current reel\n\n"
        "<b>Trading bot commands:</b>\n"
        "  /status    — bot health + Ollama status\n"
        "  /trades    — last 10 trade decisions\n\n"
        "<b>System:</b>\n"
        "  /stop      — graceful shutdown",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /status  — bot health + Ollama ping + last trade
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    await update.message.reply_text("🔍 Checking status...")

    ollama_status = _ping_ollama()

    trades = _read_last_trades(1)
    last_trade = trades[-1] if trades else "No trades logged yet."
    # Trim the raw log line to just the decision part for readability
    if " | " in last_trade:
        parts = last_trade.split(" | ")
        last_trade = " | ".join(parts[1:]) if len(parts) > 1 else last_trade

    with _pending_lock:
        pipeline_status = (
            f"⏳ Reel pending: <code>{Path(_pending['reel_path']).name}</code>"
            if _pending else "✅ No reel pending"
        )

    await update.message.reply_text(
        f"🦾 <b>ClawBot Status</b> — {_now()}\n\n"
        f"🧠 Ollama: {ollama_status}\n"
        f"🎬 Pipeline: {pipeline_status}\n\n"
        f"📊 <b>Last trade:</b>\n<code>{last_trade}</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /trades  — last N trade decisions
# ---------------------------------------------------------------------------

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    # Optional arg: /trades 5  (default 10)
    n = 10
    if context.args:
        try:
            n = max(1, min(int(context.args[0]), 25))
        except ValueError:
            pass

    trades = _read_last_trades(n)

    if not trades:
        await update.message.reply_text(
            "📊 No trade decisions logged yet.\n"
            "Run the DCA or Futures bot to generate decisions."
        )
        return

    lines = []
    for raw in trades:
        # Format: "2026-03-31T12:00:00Z INFO openclaw: TRADE_DECISION | timestamp | decision"
        if " | " in raw:
            parts = raw.split(" | ")
            # parts[1] = timestamp, parts[2] = decision
            ts = parts[1].replace("Z", "").replace("T", " ")[:16] if len(parts) > 1 else ""
            decision = parts[2] if len(parts) > 2 else raw
            lines.append(f"• <code>{ts}</code> {decision[:120]}")
        else:
            lines.append(f"• {raw[:140]}")

    header = f"📊 <b>Last {len(trades)} trade decisions:</b>\n\n"
    await update.message.reply_text(
        header + "\n".join(lines),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /pipeline  — content pipeline status
# ---------------------------------------------------------------------------

async def cmd_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    with _pending_lock:
        has_pending = bool(_pending)
        reel_name = Path(_pending["reel_path"]).name if has_pending else ""

    if has_pending:
        await update.message.reply_text(
            f"🎬 <b>Pipeline status</b>\n\n"
            f"⏳ <b>Reel awaiting approval:</b>\n"
            f"<code>{reel_name}</code>\n\n"
            f"Reply /approve to post to TikTok + Instagram\n"
            f"Reply /reject to discard",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "🎬 <b>Pipeline status</b>\n\n"
            "✅ No reel pending.\n\n"
            "Send me a video to start the pipeline!",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# /approve  — post pending reel to socials
# ---------------------------------------------------------------------------

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    with _pending_lock:
        if not _pending:
            await update.message.reply_text(
                "No reel pending approval. Send me a video first!"
            )
            return
        reel_path = Path(_pending["reel_path"])
        captions = _pending["captions"]
        _pending.clear()

    await update.message.reply_text("✅ Approved! Posting to TikTok + Instagram...")

    # Run posting in a thread — use bot.send_message (not update.message)
    # so the thread doesn't touch the async event loop directly
    bot = context.bot
    chat_id = update.effective_chat.id

    def _post() -> None:
        from content.poster import post_to_socials_sync
        import asyncio
        try:
            results = post_to_socials_sync(reel_path, captions)
            asyncio.run(bot.send_message(
                chat_id=chat_id,
                text=f"🚀 <b>Posted!</b>\n\n{results}",
                parse_mode="HTML",
            ))
        except Exception as exc:
            asyncio.run(bot.send_message(
                chat_id=chat_id,
                text=f"🚨 <b>Post failed:</b>\n<code>{exc}</code>",
                parse_mode="HTML",
            ))

    threading.Thread(target=_post, daemon=True).start()


# ---------------------------------------------------------------------------
# /reject  — discard pending reel
# ---------------------------------------------------------------------------

async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    with _pending_lock:
        if not _pending:
            await update.message.reply_text("No reel pending.")
            return
        reel_path = Path(_pending.pop("reel_path", ""))
        _pending.clear()

    if reel_path.exists():
        reel_path.unlink()

    await update.message.reply_text(
        "🗑 Reel rejected and deleted.\nSend me another video!"
    )


# ---------------------------------------------------------------------------
# /stop  — graceful shutdown
# ---------------------------------------------------------------------------

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text("👋 ClawBot shutting down. Goodbye!")
    os.kill(os.getpid(), signal.SIGINT)


# ---------------------------------------------------------------------------
# Video message handler
# ---------------------------------------------------------------------------

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
        "📥 <b>Video received!</b>\n\n"
        "Starting pipeline:\n"
        "  ⚙️ Edit to 9:16 + Whisper captions\n"
        "  🎵 Mix background music\n"
        "  🧠 Generate AI captions\n\n"
        "I'll send the finished reel when it's ready — this takes a few minutes.",
        parse_mode="HTML",
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="openclaw_"))
    suffix = ".mp4"
    if hasattr(video, "file_name") and video.file_name:
        suffix = Path(video.file_name).suffix or ".mp4"
    tmp_path = tmp_dir / f"raybans_{update.message.message_id}{suffix}"

    tg_file = await context.bot.get_file(video.file_id)
    await tg_file.download_to_drive(str(tmp_path))

    chat_id = update.effective_chat.id
    app = context.application
    threading.Thread(
        target=_run_pipeline_in_background,
        args=(tmp_path, chat_id, app),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    _app = Application.builder().token(token).build()

    _app.add_handler(CommandHandler("start",    cmd_start))
    _app.add_handler(CommandHandler("status",   cmd_status))
    _app.add_handler(CommandHandler("trades",   cmd_trades))
    _app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    _app.add_handler(CommandHandler("approve",  cmd_approve))
    _app.add_handler(CommandHandler("reject",   cmd_reject))
    _app.add_handler(CommandHandler("stop",     cmd_stop))
    _app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    print("🦾 ClawBot receiver is running.")
    print("   Commands: /start /status /trades /pipeline /approve /reject /stop")
    _app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
