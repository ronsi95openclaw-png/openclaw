"""
Voice Handler — ClawBot
========================
Transcribes Telegram voice messages using OpenAI Whisper (local),
then routes the transcription into the normal chat pipeline.

Setup:
  pip install openai-whisper
  (ffmpeg must be on PATH — Windows: winget install ffmpeg)

Falls back gracefully if Whisper not installed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from security.whitelist import is_authorized

logger = logging.getLogger("openclaw.voice")

# ── Constants ─────────────────────────────────────────────────────────────────

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")   # tiny/base/small/medium/large
WORKSPACE_ROOT = Path(__file__).parent.parent.resolve()
_AUDIO_TMP_DIR = WORKSPACE_ROOT / "data" / "voice_tmp"
_AUDIT_LOG = WORKSPACE_ROOT / "data" / "logs" / "command_audit.log"
_MAX_VOICE_SECONDS = 120   # reject clips longer than 2 min
_whisper_model = None      # lazy-loaded singleton


# ── Whisper availability check ────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def is_whisper_available() -> bool:
    """Return True if openai-whisper and ffmpeg are both available."""
    try:
        import whisper  # noqa: F401
        return _ffmpeg_available()
    except ImportError:
        return False


# ── Lazy model loader ─────────────────────────────────────────────────────────

def _load_whisper_model():
    """Load the Whisper model as a lazy singleton. Raises RuntimeError if not installed."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    try:
        import whisper
        _whisper_model = whisper.load_model(WHISPER_MODEL)
        logger.info(f"Whisper model '{WHISPER_MODEL}' loaded")
        return _whisper_model
    except ImportError:
        raise RuntimeError(
            "openai-whisper not installed. Run: pip install openai-whisper\n"
            "Also install ffmpeg: winget install ffmpeg"
        )


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(action: str, user_id: int, detail: str) -> None:
    """Append a structured JSON line to the audit log. Never logs audio content."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "user_id": user_id,
            "detail": detail,
        }
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning(f"Audit log write failed: {exc}")


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe_ogg(ogg_path: Path) -> str:
    """
    Transcribe an OGG audio file using Whisper.

    Checks duration against _MAX_VOICE_SECONDS before transcribing.
    Returns the stripped transcription text.
    Raises RuntimeError for install issues or ValueError for duration violations.
    """
    model = _load_whisper_model()

    # Check duration before transcribing
    try:
        import whisper.audio as _wa
        import numpy as np
        audio = _wa.load_audio(str(ogg_path))
        duration_seconds = len(audio) / _wa.SAMPLE_RATE
        if duration_seconds > _MAX_VOICE_SECONDS:
            raise ValueError(
                f"Voice message is {duration_seconds:.0f}s — "
                f"must be under {_MAX_VOICE_SECONDS}s (2 minutes)."
            )
    except ValueError:
        raise
    except FileNotFoundError as exc:
        if "ffmpeg" in str(exc).lower():
            raise RuntimeError(
                "ffmpeg not found. Install it with: winget install ffmpeg"
            ) from exc
        raise
    except OSError as exc:
        if "ffmpeg" in str(exc).lower() or "not found" in str(exc).lower():
            raise RuntimeError(
                "ffmpeg not found. Install it with: winget install ffmpeg"
            ) from exc
        logger.warning(f"Could not check audio duration: {exc}")
    except Exception as exc:
        logger.warning(f"Could not check audio duration: {exc}")
        # Non-fatal — proceed with transcription

    try:
        result = model.transcribe(str(ogg_path), language="en", fp16=False)
    except (FileNotFoundError, OSError) as exc:
        if "ffmpeg" in str(exc).lower() or "not found" in str(exc).lower():
            raise RuntimeError(
                "ffmpeg not found. Install it with: winget install ffmpeg"
            ) from exc
        raise

    return result["text"].strip()


# ── Main async handler ────────────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Telegram MessageHandler for VOICE messages.

    Flow:
      1. Auth check (silent reject if unauthorized)
      2. Send "Transcribing..." status message
      3. Download OGG to temp dir
      4. Transcribe in executor (blocking call off event loop)
      5. Audit log + cleanup temp file
      6. Edit status message with result or error
      7. Route transcription through brain (ask_hybrid)
    """
    chat_id: int = update.effective_chat.id

    # 1. Authorization check — silent return for unknown users
    if not is_authorized(chat_id):
        return

    msg = update.message
    status_msg = await msg.reply_text("🎙️ Transcribing...")

    ogg_path: Optional[Path] = None
    try:
        # 3. Download voice file
        _AUDIO_TMP_DIR.mkdir(parents=True, exist_ok=True)
        ogg_path = _AUDIO_TMP_DIR / f"{msg.message_id}.ogg"

        voice = await msg.voice.get_file()
        await voice.download_to_drive(ogg_path)

        # Sanity-check path stays within workspace
        ogg_path.resolve().relative_to(WORKSPACE_ROOT)

        # 4. Transcribe in executor (Whisper is blocking/CPU-intensive)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, transcribe_ogg, ogg_path)

        # 5. Audit — only metadata, never audio content
        _audit(
            action="voice_transcribed",
            user_id=chat_id,
            detail=f"chars={len(text)} message_id={msg.message_id}",
        )

        # 6. Handle empty transcription
        if not text:
            await status_msg.edit_text("🎙️ Could not understand audio.")
            return

        # 7a. Edit the status message to show the transcription
        await status_msg.edit_text(
            f"🎙️ <b>Voice transcription:</b>\n<i>\"{text}\"</i>",
            parse_mode="HTML",
        )

        # 7b. Route transcription through the brain exactly as if typed
        from core.brain import ask_hybrid
        from core.conversation import add_message, get_history

        add_message(chat_id, "user", text)
        history = get_history(chat_id)

        # ask_hybrid is synchronous — run in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        reply_tuple = await loop.run_in_executor(
            None, lambda: ask_hybrid(text, history=history)
        )
        # ask_hybrid returns (response_text, brain_used)
        response, brain_used = reply_tuple if isinstance(reply_tuple, tuple) else (reply_tuple, "llm")
        add_message(chat_id, "assistant", response)

        await msg.reply_text(f"{response}\n\n_🧠 via {brain_used}_", parse_mode="Markdown")

    except ValueError as exc:
        # Duration / validation errors
        err_msg = str(exc)
        _audit("voice_rejected", chat_id, err_msg)
        await status_msg.edit_text(f"🎙️ {err_msg}")

    except RuntimeError as exc:
        # Whisper not installed or ffmpeg missing
        err_str = str(exc)
        _audit("voice_error", chat_id, "whisper_unavailable")
        if "ffmpeg" in err_str.lower():
            await status_msg.edit_text(
                "🎙️ <b>ffmpeg not found.</b>\n"
                "Install it with:\n<code>winget install ffmpeg</code>",
                parse_mode="HTML",
            )
        else:
            await status_msg.edit_text(
                "🎙️ <b>Voice transcription not set up.</b>\n\n"
                "To enable it, run:\n"
                "<code>pip install openai-whisper</code>\n"
                "<code>winget install ffmpeg</code>",
                parse_mode="HTML",
            )

    except Exception as exc:
        logger.exception(f"Voice handler error for user {chat_id}: {exc}")
        _audit("voice_error", chat_id, f"unexpected: {type(exc).__name__}")
        try:
            await status_msg.edit_text("🎙️ An error occurred processing your voice message.")
        except Exception:
            pass

    finally:
        # Always clean up temp audio file
        if ogg_path is not None:
            try:
                ogg_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(f"Could not delete temp audio file {ogg_path}: {exc}")


# ── Fallback handler (Whisper unavailable) ────────────────────────────────────

async def handle_voice_not_available(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Fallback handler used when Whisper is confirmed unavailable at startup.
    Politely informs the user that voice transcription is not yet set up.
    """
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🎙️ <b>Voice transcription is not enabled.</b>\n\n"
        "To enable it:\n"
        "1. <code>pip install openai-whisper</code>\n"
        "2. Install ffmpeg: <code>winget install ffmpeg</code>\n"
        "3. Restart ClawBot\n\n"
        "Once set up, you can send voice notes and I'll transcribe and respond!",
        parse_mode="HTML",
    )
