"""Telegram bot receiver for OpenClaw — ClawBot v0.5.

Full command list:
  Content pipeline:
    Send video → auto-edit + AI captions → /approve or /reject

  AI assistant:
    /ask [question]     — hybrid AI answer (Ollama or Claude Haiku)
    /plan [idea]        — full business/project plan
    /research [topic]   — deep research breakdown
    /clear              — reset conversation memory

  Trading & market:
    /market             — BTC/ETH/SOL live prices + AI analysis
    /trades [n]         — last N trade decisions from log
    /status             — bot health, Ollama, last trade

  Tasks & reminders:
    /remind HH:MM text  — set a daily reminder (UTC)
    /tasks              — list pending reminders
    /cancel <id>        — cancel a reminder

  Pipeline & system:
    /pipeline           — content pipeline status
    /approve            — post pending reel to TikTok + Instagram
    /reject             — discard pending reel
    /brain              — show which AI is active + usage stats
    /stop               — graceful shutdown

Run with:
    python -m content.receiver
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load .env BEFORE any project imports so whitelist/brain pick up env vars
from dotenv import load_dotenv
load_dotenv(override=True)

import re
import signal
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.brain import CLAWBOT_SYSTEM, ask_hybrid, classify_complexity, get_usage_today
from core.conversation import add_message, clear_history, get_history
from core import scheduler as sched
from security.whitelist import is_authorized

logger = logging.getLogger("openclaw.receiver")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_pending_lock = threading.Lock()
_pending: dict = {}   # reel_path, captions, chat_id

_app: Optional[Application] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _read_last_trades(n: int = 10) -> list[str]:
    log_file = Path(__file__).parent.parent / "data" / "logs" / "trades.log"
    if not log_file.exists():
        return []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        return [l for l in lines if "TRADE_DECISION" in l][-n:]
    except Exception:
        return []


def _ping_ollama() -> str:
    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        if not models:
            return "offline ❌ (no models)"
        if model not in models:
            return f"online ✅ (using {models[0]} — pull {model} for production)"
        return "online ✅"
    except Exception as exc:
        return f"offline ❌ ({exc})"


def _run_pipeline_in_background(video_path: Path, chat_id: int, app: Application) -> None:
    from content.pipeline import process
    from content.uploader import send_for_approval_sync
    try:
        result = process(video_path, return_artifacts=True)
        if result is None:
            return
        reel_path, captions = result
        with _pending_lock:
            _pending.clear()
            _pending.update({"reel_path": str(reel_path), "captions": captions, "chat_id": chat_id})
        send_for_approval_sync(reel_path, captions)
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
        asyncio.run(app.bot.send_message(
            chat_id=chat_id,
            text=f"🚨 <b>Pipeline failed</b>\n<code>{exc}</code>",
            parse_mode="HTML",
        ))


def _confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=f"confirm:{action}"),
        InlineKeyboardButton("❌ No",  callback_data="confirm:cancel"),
    ]])


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot is online</b>\n\n"
        "<b>AI Assistant:</b>\n"
        "  /ask [question]    — ask me anything\n"
        "  /plan [idea]       — full action plan\n"
        "  /research [topic]  — deep research\n"
        "  /clear             — reset memory\n\n"
        "<b>Market & Trading:</b>\n"
        "  /market            — BTC/ETH/SOL + analysis\n"
        "  /trades [n]        — last trade decisions\n\n"
        "<b>Reminders:</b>\n"
        "  /remind 08:00 text — set daily reminder (UTC)\n"
        "  /tasks             — list reminders\n\n"
        "<b>Content Pipeline:</b>\n"
        "  Send a video       — auto-edit + AI captions\n"
        "  /pipeline          — pipeline status\n"
        "  /approve           — post to TikTok + Instagram\n"
        "  /reject            — discard reel\n\n"
        "<b>System:</b>\n"
        "  /status            — bot health\n"
        "  /brain             — AI usage stats\n"
        "  /stop              — shutdown",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /ask — hybrid AI answer
# ---------------------------------------------------------------------------

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ask [your question]")
        return

    prompt = " ".join(context.args)
    chat_id = update.effective_chat.id
    complexity = classify_complexity(prompt)
    brain_label = "Claude Haiku ⚡" if complexity == "complex" else "Ollama 🧠"

    thinking_msg = await update.message.reply_text(
        f"<i>Thinking via {brain_label}...</i>", parse_mode="HTML"
    )

    history = get_history(chat_id)
    add_message(chat_id, "user", prompt)

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, history=history)
        add_message(chat_id, "assistant", response)

        await thinking_msg.edit_text(
            f"🦾 <b>ClawBot</b> <i>(via {brain})</i>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /plan — full business/project plan
# ---------------------------------------------------------------------------

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /plan [your business or project idea]")
        return

    idea = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        "<i>Building your plan via Claude Haiku ⚡...</i>", parse_mode="HTML"
    )

    prompt = (
        f"Create a structured action plan for: {idea}\n\n"
        "Format with these sections:\n"
        "OVERVIEW — 2 sentences\n"
        "PROS — 3 bullet points\n"
        "CONS / RISKS — 3 bullet points\n"
        "ACTION PLAN — 5 numbered steps\n"
        "RESOURCES NEEDED — list\n"
        "TIME + COST ESTIMATE — brief\n\n"
        "Be direct and actionable. Format for Telegram."
    )

    chat_id = update.effective_chat.id
    history = get_history(chat_id)
    add_message(chat_id, "user", f"/plan {idea}")

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, history=history, force="complex")
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"📋 <b>Plan: {idea[:40]}</b>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /research — deep research breakdown
# ---------------------------------------------------------------------------

async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /research [topic]")
        return

    topic = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        "<i>Researching via Claude Haiku ⚡...</i>", parse_mode="HTML"
    )

    prompt = (
        f"Do a research breakdown on: {topic}\n\n"
        "Format with:\n"
        "SUMMARY — 2-3 sentences\n"
        "KEY POINTS — 5 bullet points\n"
        "WHAT TO WATCH — 3 things to monitor\n"
        "RECOMMENDATION — 1 clear action\n\n"
        "Be direct. Format for Telegram."
    )

    chat_id = update.effective_chat.id
    history = get_history(chat_id)
    add_message(chat_id, "user", f"/research {topic}")

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, history=history, force="complex")
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"🔬 <b>Research: {topic[:40]}</b>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /clear — reset conversation memory
# ---------------------------------------------------------------------------

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    clear_history(update.effective_chat.id)
    await update.message.reply_text("🗑 Conversation memory cleared. Fresh start!")


# ---------------------------------------------------------------------------
# /market — live crypto prices + AI analysis
# ---------------------------------------------------------------------------

async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    thinking_msg = await update.message.reply_text(
        "<i>Fetching prices and analysing...</i>", parse_mode="HTML"
    )
    try:
        from core.market import get_market_summary
        summary = get_market_summary()
        await thinking_msg.edit_text(summary, parse_mode="HTML")
    except Exception as exc:
        await thinking_msg.edit_text(
            f"🚨 Market data unavailable: <code>{exc}</code>", parse_mode="HTML"
        )


# ---------------------------------------------------------------------------
# /remind — set a daily reminder
# ---------------------------------------------------------------------------

async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /remind HH:MM your reminder text\n"
            "Example: /remind 08:00 Check crypto markets\n"
            "<i>(Times are in UTC)</i>",
            parse_mode="HTML",
        )
        return

    time_str = context.args[0]
    text = " ".join(context.args[1:])
    chat_id = update.effective_chat.id

    try:
        task = sched.add_reminder(chat_id, time_str, text)
        await update.message.reply_text(
            f"✅ <b>Reminder set!</b>\n\n"
            f"⏰ Time: <code>{task['time']} UTC</code>\n"
            f"📝 Text: {text}\n\n"
            f"<i>ID: <code>{task['id']}</code></i>",
            parse_mode="HTML",
        )
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")


# ---------------------------------------------------------------------------
# /tasks — list pending reminders
# ---------------------------------------------------------------------------

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    reminders = sched.get_reminders(update.effective_chat.id)
    if not reminders:
        await update.message.reply_text(
            "📋 No pending reminders.\n\nUse /remind HH:MM text to add one."
        )
        return

    lines = ["📋 <b>Pending Reminders:</b>\n"]
    for r in reminders:
        lines.append(
            f"⏰ <code>{r['time']} UTC</code> — {r['text']}\n"
            f"   <i>ID: <code>{r['id']}</code></i>\n"
            f"   Use /cancel <code>{r['id']}</code> to remove"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /cancel — cancel a reminder
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cancel <task_id>")
        return
    task_id = context.args[0]
    if sched.cancel_reminder(task_id):
        await update.message.reply_text(f"✅ Reminder <code>{task_id}</code> cancelled.", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Reminder not found or already completed.")


# ---------------------------------------------------------------------------
# /status — bot health
# ---------------------------------------------------------------------------

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text("<i>Checking status...</i>", parse_mode="HTML")

    ollama_status = _ping_ollama()
    trades = _read_last_trades(1)
    last_trade = trades[-1] if trades else "No trades logged yet."
    if " | " in last_trade:
        parts = last_trade.split(" | ")
        last_trade = " | ".join(parts[1:]) if len(parts) > 1 else last_trade

    with _pending_lock:
        pipeline_status = (
            f"Reel pending: <code>{Path(_pending['reel_path']).name}</code>"
            if _pending else "No reel pending"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    claude_status = "configured ✅" if api_key else "not set ⚠️"

    await update.message.reply_text(
        f"🦾 <b>ClawBot Status</b> — {_now()}\n\n"
        f"🧠 Ollama: {ollama_status}\n"
        f"⚡ Claude API: {claude_status}\n"
        f"🎬 Pipeline: {pipeline_status}\n\n"
        f"📊 <b>Last trade:</b>\n<code>{last_trade[:200]}</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /trades — last N trade decisions
# ---------------------------------------------------------------------------

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    n = 10
    if context.args:
        try:
            n = max(1, min(int(context.args[0]), 25))
        except ValueError:
            pass

    trades = _read_last_trades(n)
    if not trades:
        await update.message.reply_text(
            "📊 No trade decisions logged yet.\nRun the DCA or Futures bot to generate decisions."
        )
        return

    lines = []
    for raw in trades:
        if " | " in raw:
            parts = raw.split(" | ")
            ts = parts[1].replace("Z", "").replace("T", " ")[:16] if len(parts) > 1 else ""
            decision = parts[2] if len(parts) > 2 else raw
            lines.append(f"• <code>{ts}</code> {decision[:120]}")
        else:
            lines.append(f"• {raw[:140]}")

    await update.message.reply_text(
        f"📊 <b>Last {len(trades)} trade decisions:</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /brain — AI usage stats
# ---------------------------------------------------------------------------

async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    stats = get_usage_today()
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    ollama_calls   = stats.get("ollama_calls", 0)
    claude_calls   = stats.get("claude_calls", 0)
    in_tokens      = stats.get("claude_input_tokens", 0)
    out_tokens     = stats.get("claude_output_tokens", 0)
    cache_hits     = stats.get("cache_hits", 0)

    # Claude Haiku pricing: $1.00/$5.00 per 1M tokens
    cost = (in_tokens * 0.000001) + (out_tokens * 0.000005)
    # Savings: cache hits avoided an API call — avg ~200 tokens saved per hit
    savings = cache_hits * 200 * 0.000001

    await update.message.reply_text(
        f"🧠 <b>ClawBot Brain — Today</b>\n\n"
        f"<b>Active brains:</b>\n"
        f"  🧠 Simple tasks: Ollama qwen2.5:14b (local / free)\n"
        f"  ⚡ Complex tasks: Claude Haiku {'✅' if api_key else '⚠️ key missing'}\n\n"
        f"<b>Usage today:</b>\n"
        f"  Ollama calls:  {ollama_calls}\n"
        f"  Claude calls:  {claude_calls}\n"
        f"  Cache hits:    {cache_hits} 💾\n"
        f"  Input tokens:  {in_tokens:,}\n"
        f"  Output tokens: {out_tokens:,}\n\n"
        f"<b>Cost today:</b>   ${cost:.4f}\n"
        f"<b>Cache saved:</b>  ~${savings:.4f}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /pipeline — content pipeline status
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
            f"⏳ Reel awaiting approval:\n<code>{reel_name}</code>\n\n"
            f"/approve — post to TikTok + Instagram\n"
            f"/reject  — discard",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "🎬 <b>Pipeline status</b>\n\n✅ No reel pending.\nSend me a video to start!",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# /approve — post reel to socials (with confirmation)
# ---------------------------------------------------------------------------

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    with _pending_lock:
        if not _pending:
            await update.message.reply_text("No reel pending. Send me a video first!")
            return
        reel_name = Path(_pending["reel_path"]).name

    await update.message.reply_text(
        f"Post <code>{reel_name}</code> to TikTok + Instagram?",
        parse_mode="HTML",
        reply_markup=_confirm_keyboard("post_reel"),
    )


# ---------------------------------------------------------------------------
# /reject — discard reel
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
    await update.message.reply_text("🗑 Reel rejected and deleted. Send me another video!")


# ---------------------------------------------------------------------------
# /caption — generate social caption without video
# ---------------------------------------------------------------------------

async def cmd_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /caption [topic or description]")
        return
    topic = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        "<i>Writing caption...</i>", parse_mode="HTML"
    )
    prompt = (
        f"Write an Instagram and TikTok caption for: {topic}\n\n"
        "Format:\n"
        "INSTAGRAM:\n[caption with emojis, 2-3 sentences, CTA]\n[20 hashtags]\n\n"
        "TIKTOK:\n[punchy 1-liner under 150 chars]\n[5-8 hashtags]"
    )
    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, force="simple")
        await thinking_msg.edit_text(
            f"✍️ <b>Captions for: {topic[:30]}</b>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /hashtags — generate niche hashtags
# ---------------------------------------------------------------------------

async def cmd_hashtags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /hashtags [niche or topic]")
        return
    niche = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        "<i>Generating hashtags...</i>", parse_mode="HTML"
    )
    prompt = (
        f"Generate hashtags for the niche: {niche}\n\n"
        "Give:\n"
        "- 10 high-volume hashtags (1M+ posts)\n"
        "- 10 mid-range hashtags (100K-1M)\n"
        "- 10 niche hashtags (<100K, high engagement)\n\n"
        "Format as 3 groups, copy-paste ready."
    )
    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, force="simple")
        await thinking_msg.edit_text(
            f"# <b>Hashtags: {niche[:30]}</b>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /dca — DCA analysis for an asset
# ---------------------------------------------------------------------------

async def cmd_dca(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    asset = " ".join(context.args) if context.args else "BTC"
    thinking_msg = await update.message.reply_text(
        f"<i>Analysing DCA opportunity for {asset}...</i>", parse_mode="HTML"
    )

    # Fetch current price for context
    price_context = ""
    try:
        import requests as req
        ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(asset.upper(), asset.lower())
        r = req.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get(ids, {})
            price_context = f"Current price: ${data.get('usd', 'N/A'):,}  (24h: {data.get('usd_24h_change', 0):.1f}%)\n"
    except Exception:
        pass

    prompt = (
        f"DCA analysis for {asset}:\n{price_context}\n"
        "Provide:\n"
        "- Should I DCA now? (Yes/No/Wait)\n"
        "- 3 reasons for your recommendation\n"
        "- Suggested entry strategy (e.g. split over X weeks)\n"
        "- Key risk to watch\n\n"
        "Be direct. This is not financial advice — it's analysis."
    )

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, force="complex")
        await thinking_msg.edit_text(
            f"📈 <b>DCA Analysis: {asset}</b>\n\n{price_context}{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /reel — trigger content pipeline from idea
# ---------------------------------------------------------------------------

async def cmd_reel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    idea = " ".join(context.args) if context.args else ""
    if idea:
        await update.message.reply_text(
            f"🎬 <b>Reel idea noted:</b> {idea}\n\n"
            "Send me the video when you're ready and I'll use this as context for the captions.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "🎬 <b>Content Pipeline</b>\n\n"
            "Send me a video (from your Ray-Ban glasses or phone) and I'll:\n"
            "  ⚙️ Auto-edit to 9:16\n"
            "  🎙 Add Whisper captions\n"
            "  🎵 Mix music\n"
            "  🧠 Write AI captions\n"
            "  📤 Send for your approval\n\n"
            "Just drop the video here!",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# /help — full command reference
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot — Command Reference</b>\n\n"
        "<b>💡 Business:</b>\n"
        "  /ask [question] — free chat\n"
        "  /plan [idea]    — full action plan\n"
        "  /research [topic] — deep research\n\n"
        "<b>📈 Crypto:</b>\n"
        "  /market         — BTC/ETH/SOL prices\n"
        "  /scan [tf]      — RSI+MACD live scan (4h default)\n"
        "  /dca [asset]    — DCA analysis\n"
        "  /trades [n]     — last trade decisions\n\n"
        "<b>🎬 Content:</b>\n"
        "  /reel [idea]    — start content pipeline\n"
        "  /caption [topic] — generate caption\n"
        "  /hashtags [niche] — generate hashtags\n"
        "  /approve        — post reel to socials\n"
        "  /reject         — discard reel\n\n"
        "<b>⏰ Tasks:</b>\n"
        "  /remind HH:MM text — set daily reminder\n"
        "  /tasks          — list reminders\n"
        "  /cancel [id]    — cancel reminder\n\n"
        "<b>⚙️ System:</b>\n"
        "  /status         — all systems status\n"
        "  /brain          — AI usage stats\n"
        "  /pipeline       — pipeline status\n"
        "  /clear          — reset memory\n"
        "  /stop           — shutdown",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /scan — RSI+MACD live scan across BTC, SOL, XRP, ETH
# ---------------------------------------------------------------------------

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    timeframe = context.args[0] if context.args else "4h"
    valid_tf   = {"1h", "4h", "1d"}
    if timeframe not in valid_tf:
        await update.message.reply_text(
            f"Usage: /scan [timeframe]\nValid: {', '.join(valid_tf)}\nDefault: 4h"
        )
        return

    thinking_msg = await update.message.reply_text(
        f"<i>Scanning BTC, SOL, XRP, ETH on {timeframe} candles...</i>",
        parse_mode="HTML",
    )

    try:
        from trading.exchange import fetch_all_closes
        from trading.strategy import RSIMACDStrategy

        strategy = RSIMACDStrategy()
        candle_data = fetch_all_closes(strategy.config.coins, timeframe=timeframe, count=100)
        signals = strategy.scan_all(candle_data)

        if not signals:
            # No BUY/SELL — show quick HOLD summary
            from trading.strategy import calculate_rsi, calculate_macd
            lines = [f"📊 <b>Market Scan — {timeframe}</b>  <i>No signals</i>\n"]
            for coin, closes in candle_data.items():
                try:
                    rsi          = calculate_rsi(closes)
                    _, _, hist   = calculate_macd(closes)
                    trend        = "↑" if hist > 0 else "↓"
                    lines.append(f"⚪ {coin}: RSI <code>{rsi:.1f}</code> {trend}")
                except Exception:
                    lines.append(f"⚪ {coin}: insufficient data")
            lines.append("\n<i>All coins in neutral zone. Watching.</i>")
            await thinking_msg.edit_text("\n".join(lines), parse_mode="HTML")
        else:
            header = f"🔔 <b>Market Scan — {timeframe} — {len(signals)} signal(s)</b>\n\n"
            parts  = [header]
            for s in signals:
                parts.append(s.to_telegram_message())
                parts.append("")
            parts.append("<i>⚠️ Analysis only. No orders placed.</i>")
            await thinking_msg.edit_text("\n".join(parts), parse_mode="HTML")

    except Exception as exc:
        await thinking_msg.edit_text(
            f"🚨 Scan failed: <code>{exc}</code>", parse_mode="HTML"
        )


# ---------------------------------------------------------------------------
# /stop — graceful shutdown
# ---------------------------------------------------------------------------

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "👋 <b>ClawBot shutting down.</b> See you next time!", parse_mode="HTML"
    )
    os.kill(os.getpid(), signal.SIGINT)


# ---------------------------------------------------------------------------
# Inline keyboard callback handler
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.message.chat.id):
        return

    data = query.data or ""

    if data == "confirm:cancel":
        await query.edit_message_text("❌ Cancelled.")
        return

    if data == "confirm:post_reel":
        with _pending_lock:
            if not _pending:
                await query.edit_message_text("No reel pending anymore.")
                return
            reel_path = Path(_pending["reel_path"])
            captions  = _pending["captions"]
            _pending.clear()

        await query.edit_message_text("✅ Approved! Posting to TikTok + Instagram...")
        bot     = context.bot
        chat_id = query.message.chat.id

        def _post() -> None:
            from content.poster import post_to_socials_sync
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
# Video message handler
# ---------------------------------------------------------------------------

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    msg   = update.message
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
        "I'll send the finished reel when ready (a few minutes).",
        parse_mode="HTML",
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="openclaw_"))
    suffix  = ".mp4"
    if hasattr(video, "file_name") and video.file_name:
        suffix = Path(video.file_name).suffix or ".mp4"
    tmp_path = tmp_dir / f"raybans_{update.message.message_id}{suffix}"

    tg_file = await context.bot.get_file(video.file_id)
    await tg_file.download_to_drive(str(tmp_path))

    chat_id = update.effective_chat.id
    app     = context.application
    threading.Thread(
        target=_run_pipeline_in_background,
        args=(tmp_path, chat_id, app),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Scheduler send function (injected so reminders can fire Telegram messages)
# ---------------------------------------------------------------------------

async def _scheduler_send(chat_id: int, text: str) -> None:
    if _app:
        await _app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    # Start APScheduler
    sched.set_send_fn(_scheduler_send)
    sched.start_scheduler()

    _app = Application.builder().token(token).build()

    # --- Commands ---
    _app.add_handler(CommandHandler("start",    cmd_start))
    _app.add_handler(CommandHandler("ask",      cmd_ask))
    _app.add_handler(CommandHandler("plan",     cmd_plan))
    _app.add_handler(CommandHandler("research", cmd_research))
    _app.add_handler(CommandHandler("clear",    cmd_clear))
    _app.add_handler(CommandHandler("market",   cmd_market))
    _app.add_handler(CommandHandler("remind",   cmd_remind))
    _app.add_handler(CommandHandler("tasks",    cmd_tasks))
    _app.add_handler(CommandHandler("cancel",   cmd_cancel))
    _app.add_handler(CommandHandler("status",   cmd_status))
    _app.add_handler(CommandHandler("trades",   cmd_trades))
    _app.add_handler(CommandHandler("brain",    cmd_brain))
    _app.add_handler(CommandHandler("caption",  cmd_caption))
    _app.add_handler(CommandHandler("hashtags", cmd_hashtags))
    _app.add_handler(CommandHandler("dca",      cmd_dca))
    _app.add_handler(CommandHandler("scan",     cmd_scan))
    _app.add_handler(CommandHandler("reel",     cmd_reel))
    _app.add_handler(CommandHandler("help",     cmd_help))
    _app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    _app.add_handler(CommandHandler("approve",  cmd_approve))
    _app.add_handler(CommandHandler("reject",   cmd_reject))
    _app.add_handler(CommandHandler("stop",     cmd_stop))
    _app.add_handler(CallbackQueryHandler(handle_callback))
    _app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    print("🦾 ClawBot is running.")
    print("   /ask /plan /research /market /remind /tasks /brain")
    print("   /status /trades /pipeline /approve /reject /stop")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
