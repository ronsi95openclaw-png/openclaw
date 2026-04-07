"""ClawBot v0.8 — Business AI Partner + Trading Bot

Just type anything to chat. Commands for structured tasks:

  AI (business partner):
    [any message]      — chat with Ollama (business partner mode)
    /ask [question]    — same, explicit
    /plan [idea]       — structured business plan
    /research [topic]  — deep research breakdown
    /clear             — reset conversation memory

  Crypto & Markets:
    /market            — BTC/ETH/SOL live prices + AI analysis
    /scan [1h|4h|1d]   — RSI+MACD live signal scan
    /dca [asset]       — DCA entry analysis
    /news              — macro news filter (BLOCK/ALLOW trading)
    /backtest          — run 4-year strategy backtest on all pairs
    /report            — trade performance report + AI analysis

  Trading Automation:
    /autotrade [on|off|now|status] — fully auto daily trading

  PC Execution:
    /run [command]     — run a shell command on this PC
    /py [code]         — run Python code on this PC

  Reminders:
    /remind HH:MM text — set a daily reminder (UTC)
    /tasks             — list pending reminders
    /cancel <id>       — cancel a reminder

  Multi-Agent Orchestration:
    /orchestrate       — manage orchestrated tasks
    /otasks            — list all tasks

  System:
    /status            — bot + Ollama health check
    /brain             — AI usage stats
    /weather [city]    — current weather
    /codereview        — AI code self-review (all project files)
    /stop              — graceful shutdown

Run with:
    python -m content.receiver
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load .env BEFORE any project imports
from dotenv import load_dotenv
load_dotenv(override=True)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.brain import CLAWBOT_SYSTEM, ask_hybrid, classify_complexity, get_usage_today
from core.conversation import add_message, clear_history, get_history
from core import scheduler as sched
from security.whitelist import is_authorized
from skills.agent_team_orchestrator import get_orchestrator
from skills.self_improving import (
    append_correction,
    append_memory,
    get_file_preview,
    get_status,
    initialize_self_improving,
    resolve_file_name,
)

logger = logging.getLogger("openclaw.receiver")


# ── Telegram helpers ──────────────────────────────────────────────────────────

import re as _re

def _safe_html(text: str) -> str:
    """Escape angle-bracket sequences that aren't valid Telegram HTML tags.

    Telegram supports only: b, i, u, s, code, pre, a, em, strong, tg-spoiler.
    Any other <tag> (e.g. <20%>, <br>, <list>) causes a BadRequest parse error.
    """
    VALID = r'(?:/?(?:b|i|u|s|em|strong|code|pre|a|tg-spoiler)(?:\s[^>]*)?)|\!--.*?--'
    def _fix(m):
        inner = m.group(0)[1:-1]  # strip < >
        if _re.fullmatch(VALID, inner.strip(), _re.IGNORECASE):
            return m.group(0)   # valid tag — keep as-is
        return m.group(0).replace('<', '&lt;').replace('>', '&gt;')
    return _re.sub(r'<[^>]+>', _fix, text)


async def _safe_reply(msg, text: str, parse_mode: str = "HTML") -> None:
    """Send a Telegram reply; fall back to plain text if HTML parse fails."""
    try:
        await msg.reply_text(_safe_html(text) if parse_mode == "HTML" else text,
                             parse_mode=parse_mode)
    except Exception:
        try:
            await msg.reply_text(text)   # plain text fallback
        except Exception:
            await msg.reply_text("⚠️ Response too long or contains unsupported formatting.")

_app: Optional[Application] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _ping_ollama() -> str:
    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        if not models:
            return "offline ❌ (no models)"
        if model not in models:
            return f"online ✅ (using {models[0]})"
        return "online ✅"
    except Exception as exc:
        return f"offline ❌ ({exc})"


async def _scheduler_send(chat_id: int, text: str) -> None:
    if _app:
        await _app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot is online — your AI business partner</b>\n\n"
        "Just <b>type anything</b> to chat. I'll reply as your business partner.\n\n"
        "<b>💡 Business AI:</b>\n"
        "  Just type — free conversation\n"
        "  /ask [question]     — explicit Q&A\n"
        "  /plan [idea]        — structured action plan\n"
        "  /research [topic]   — deep research\n"
        "  /clear              — reset memory\n\n"
        "<b>📈 Crypto:</b>\n"
        "  /market             — live prices + analysis\n"
        "  /scan [1h|4h|1d]   — RSI+MACD signal scan\n"
        "  /dca [asset]        — DCA entry analysis\n\n"
        "<b>💻 PC Execution:</b>\n"
        "  /run [command]      — run shell command\n"
        "  /py [code]          — run Python code\n\n"
        "<b>⏰ Reminders:</b>\n"
        "  /remind HH:MM text  — set daily reminder\n"
        "  /tasks              — list reminders\n\n"
        "<b>📓 Knowledge Base:</b>\n"
        "  /save [text]        — save last chat or a custom note\n"
        "  /notes              — view all saved notes\n"
        "  /notes [topic]      — search notes\n\n"
        "<b>🧠 Self-Improving:</b>\n"
        "  /selfimprove init               — create ~/self-improving/ memory files\n"
        "  /selfimprove status             — show memory and correction counts\n"
        "  /selfimprove show memory|corrections|heartbeat|index\n"
        "  /selfimprove log correction ... — log a correction entry\n"
        "  /selfimprove log memory ...     — log a reusable lesson\n\n"
        "<b>⚙️ System:</b>\n"
        "  /status  /brain  /weather [city]  /restart  /stop\n\n"
        "<b>🚀 Auto-Upgrade:</b>\n"
        "  /upgrade           — dry run: preview what would be fixed\n"
        "  /upgrade apply     — apply LLM fixes + auto-restart\n"
        "  /upgrade review    — run code review then auto-fix",
        parse_mode="HTML",
    )


# ── /save ─────────────────────────────────────────────────────────────────────

async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a note or the last conversation exchange to the knowledge base."""
    if not is_authorized(update.effective_chat.id):
        return

    from core.knowledge import save_note, save_conversation_exchange, format_note_saved
    from core.conversation import get_history

    chat_id = update.effective_chat.id
    args    = " ".join(context.args).strip()

    if args:
        # /save some text or idea → save as custom note
        note = save_note(args, source="telegram")
    else:
        # /save with no args → save last bot↔user exchange from history
        history = get_history(chat_id)
        if len(history) < 2:
            await update.message.reply_text(
                "💬 <b>Nothing to save yet.</b>\n\n"
                "Have a conversation first, then /save to keep it.\n"
                "Or: /save your idea here",
                parse_mode="HTML",
            )
            return
        # Pull last user + assistant pair
        user_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        bot_msg  = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
        note = save_conversation_exchange(user_msg, bot_msg)

    await update.message.reply_text(format_note_saved(note), parse_mode="HTML")


# ── /notes ────────────────────────────────────────────────────────────────────

async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List or search saved notes."""
    if not is_authorized(update.effective_chat.id):
        return

    from core.knowledge import get_notes, delete_note, get_note_by_id, format_notes_list

    args = context.args

    # /notes delete <id>
    if args and args[0].lower() == "delete" and len(args) > 1:
        note_id = args[1]
        if delete_note(note_id):
            await update.message.reply_text(
                f"🗑 Note <code>{note_id}</code> deleted.", parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"❌ Note <code>{note_id}</code> not found.", parse_mode="HTML"
            )
        return

    # /notes <id> — view full note
    if args and len(args[0]) == 8 and not args[0].isalpha():
        note = get_note_by_id(args[0])
        if note:
            ts   = note.get("timestamp", "")[:16].replace("T", " ")
            tags = " ".join(f"#{t}" for t in note.get("tags", [])) or "none"
            await update.message.reply_text(
                f"🗒 <b>{note['title']}</b>\n"
                f"📅 {ts} UTC | 🏷 {tags}\n\n"
                f"{note['content']}",
                parse_mode="HTML",
            )
            return

    # /notes [search term]
    search = " ".join(args) if args else None
    notes  = get_notes(limit=8, search=search)
    await update.message.reply_text(
        format_notes_list(notes, search=search), parse_mode="HTML"
    )


# ── Free-text conversation (business partner mode) ────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any plain text message → Ollama conversation (business partner persona)."""
    if not is_authorized(update.effective_chat.id):
        return

    text    = update.message.text.strip()
    chat_id = update.effective_chat.id

    thinking_msg = await update.message.reply_text(
        "<i>Thinking...</i>", parse_mode="HTML"
    )

    history = get_history(chat_id)
    add_message(chat_id, "user", text)

    try:
        response, brain = ask_hybrid(text, system=CLAWBOT_SYSTEM, history=history)
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"🦾 <b>ClawBot</b> <i>({brain})</i>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ── /ask ──────────────────────────────────────────────────────────────────────

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /ask [your question]")
        return

    prompt  = " ".join(context.args)
    chat_id = update.effective_chat.id
    complexity  = classify_complexity(prompt)
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
            f"🦾 <b>ClawBot</b> <i>({brain})</i>\n\n{response}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ── /plan ─────────────────────────────────────────────────────────────────────

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /plan [your idea]")
        return

    idea         = " ".join(context.args)
    chat_id      = update.effective_chat.id
    thinking_msg = await update.message.reply_text(
        "<i>Building plan via Claude Haiku ⚡...</i>", parse_mode="HTML"
    )

    prompt = (
        f"Create a structured action plan for: {idea}\n\n"
        "Format:\n"
        "OVERVIEW — 2 sentences\n"
        "PROS — 3 bullet points\n"
        "CONS / RISKS — 3 bullet points\n"
        "ACTION PLAN — 5 numbered steps\n"
        "RESOURCES NEEDED — list\n"
        "TIME + COST ESTIMATE — brief\n\n"
        "Be direct and actionable. Format for Telegram."
    )

    history = get_history(chat_id)
    add_message(chat_id, "user", f"/plan {idea}")

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, history=history, force="complex")
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"📋 <b>Plan: {idea[:40]}</b>\n\n{response}", parse_mode="HTML"
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ── /research ─────────────────────────────────────────────────────────────────

async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /research [topic]")
        return

    topic        = " ".join(context.args)
    chat_id      = update.effective_chat.id
    thinking_msg = await update.message.reply_text(
        "<i>Researching via Claude Haiku ⚡...</i>", parse_mode="HTML"
    )

    prompt = (
        f"Research breakdown on: {topic}\n\n"
        "Format:\n"
        "SUMMARY — 2-3 sentences\n"
        "KEY POINTS — 5 bullet points\n"
        "WHAT TO WATCH — 3 things to monitor\n"
        "RECOMMENDATION — 1 clear action\n\n"
        "Be direct. Format for Telegram."
    )

    history = get_history(chat_id)
    add_message(chat_id, "user", f"/research {topic}")

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, history=history, force="complex")
        add_message(chat_id, "assistant", response)
        full_text = f"🔬 <b>Research: {topic[:40]}</b>\n\n{_safe_html(response)}"
        try:
            await thinking_msg.edit_text(full_text, parse_mode="HTML")
        except Exception:
            await thinking_msg.edit_text(f"🔬 Research: {topic[:40]}\n\n{response}")
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ── /clear ────────────────────────────────────────────────────────────────────

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    clear_history(update.effective_chat.id)
    await update.message.reply_text("🗑 Conversation memory cleared. Fresh start!")


# ── /market ───────────────────────────────────────────────────────────────────

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


# ── /scan ─────────────────────────────────────────────────────────────────────

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    timeframe = context.args[0] if context.args else "4h"
    if timeframe not in {"1h", "4h", "1d"}:
        await update.message.reply_text("Usage: /scan [1h|4h|1d]  (default: 4h)")
        return

    thinking_msg = await update.message.reply_text(
        f"<i>Scanning BTC, SOL, XRP, ETH on {timeframe} candles...</i>",
        parse_mode="HTML",
    )

    try:
        from trading.exchange import fetch_all_closes
        from trading.strategy import RSIMACDStrategy, calculate_rsi, calculate_macd

        strategy    = RSIMACDStrategy()
        candle_data = fetch_all_closes(strategy.config.coins, timeframe=timeframe, count=100)
        signals     = strategy.scan_all(candle_data)

        if not signals:
            lines = [f"📊 <b>Market Scan — {timeframe}</b>  <i>No signals</i>\n"]
            for coin, closes in candle_data.items():
                try:
                    rsi        = calculate_rsi(closes)
                    _, _, hist = calculate_macd(closes)
                    trend      = "↑" if hist > 0 else "↓"
                    # Proximity warnings
                    if rsi >= 68:
                        icon = "🔴"
                        warn = f"  ⚠️ near overbought ({rsi:.1f})"
                    elif rsi <= 32:
                        icon = "🟢"
                        warn = f"  ⚠️ near oversold ({rsi:.1f})"
                    else:
                        icon = "⚪"
                        warn = ""
                    macd_str = f"MACD <code>{hist:+.1f}</code> {trend}"
                    lines.append(f"{icon} {coin}: RSI <code>{rsi:.1f}</code> | {macd_str}{warn}")
                except Exception:
                    lines.append(f"⚪ {coin}: insufficient data")
            lines.append("\n<i>Waiting for RSI + MACD crossover confirmation to signal.</i>")
            await thinking_msg.edit_text("\n".join(lines), parse_mode="HTML")
        else:
            parts = [f"🔔 <b>Scan — {timeframe} — {len(signals)} signal(s)</b>\n"]
            for s in signals:
                parts.append(s.to_telegram_message())
                parts.append("")
            parts.append("<i>⚠️ Analysis only. No orders placed.</i>")
            await thinking_msg.edit_text("\n".join(parts), parse_mode="HTML")

    except Exception as exc:
        await thinking_msg.edit_text(
            f"🚨 Scan failed: <code>{exc}</code>", parse_mode="HTML"
        )


# ── /dca ──────────────────────────────────────────────────────────────────────

async def cmd_dca(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    asset        = " ".join(context.args).upper() if context.args else "BTC"
    thinking_msg = await update.message.reply_text(
        f"<i>DCA analysis for {asset}...</i>", parse_mode="HTML"
    )

    price_context = ""
    try:
        import requests as req
        ids = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple"}.get(asset, asset.lower())
        r   = req.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get(ids, {})
            price_context = f"Price: ${data.get('usd', 'N/A'):,}  24h: {data.get('usd_24h_change', 0):.1f}%\n"
    except Exception:
        pass

    prompt = (
        f"DCA analysis for {asset}:\n{price_context}\n"
        "- Should I DCA now? (Yes/No/Wait)\n"
        "- 3 reasons\n"
        "- Suggested entry strategy\n"
        "- Key risk to watch\n\n"
        "Be direct. Not financial advice — analysis only."
    )

    try:
        response, brain = ask_hybrid(prompt, system=CLAWBOT_SYSTEM, force="complex")
        await thinking_msg.edit_text(
            f"📈 <b>DCA: {asset}</b>\n\n{price_context}{response}", parse_mode="HTML"
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{exc}</code>", parse_mode="HTML")


# ── /run — execute shell command on this PC ───────────────────────────────────

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /run [shell command]\n"
            "Example: /run dir\n"
            "Example: /run tasklist | findstr python"
        )
        return

    command = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        f"<i>Running:</i> <code>{command}</code>", parse_mode="HTML"
    )

    def _execute():
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(Path(__file__).parent.parent),
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output = stdout or stderr or "(no output)"
            rc     = result.returncode
            return output, rc
        except subprocess.TimeoutExpired:
            return "Timed out after 30 seconds.", 1
        except Exception as exc:
            return str(exc), 1

    loop   = asyncio.get_event_loop()
    output, rc = await loop.run_in_executor(None, _execute)

    # Truncate if too long for Telegram (4096 char limit)
    if len(output) > 3500:
        output = output[:3500] + "\n... (truncated)"

    status = "✅" if rc == 0 else "❌"
    await thinking_msg.edit_text(
        f"{status} <b>/run</b> <code>{command}</code>\n\n<pre>{output}</pre>",
        parse_mode="HTML",
    )


# ── /py — execute Python code on this PC ─────────────────────────────────────

async def cmd_py(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /py [python code]\n"
            "Example: /py print(2 + 2)\n"
            "Example: /py import os; print(os.getcwd())"
        )
        return

    code         = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        f"<i>Running Python:</i> <code>{code}</code>", parse_mode="HTML"
    )

    def _execute():
        try:
            python = str(Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe")
            result = subprocess.run(
                [python, "-c", code],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(Path(__file__).parent.parent),
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            output = stdout or stderr or "(no output)"
            return output, result.returncode
        except subprocess.TimeoutExpired:
            return "Timed out after 30 seconds.", 1
        except Exception as exc:
            return str(exc), 1

    loop   = asyncio.get_event_loop()
    output, rc = await loop.run_in_executor(None, _execute)

    if len(output) > 3500:
        output = output[:3500] + "\n... (truncated)"

    status = "✅" if rc == 0 else "❌"
    await thinking_msg.edit_text(
        f"{status} <b>/py</b>\n<code>{code}</code>\n\n<pre>{output}</pre>",
        parse_mode="HTML",
    )


# ── /remind ───────────────────────────────────────────────────────────────────

async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /remind HH:MM your text\n"
            "Example: /remind 08:00 Check crypto markets\n"
            "<i>(Times in UTC)</i>",
            parse_mode="HTML",
        )
        return

    time_str = context.args[0]
    text     = " ".join(context.args[1:])
    chat_id  = update.effective_chat.id

    try:
        task = sched.add_reminder(chat_id, time_str, text)
        await update.message.reply_text(
            f"✅ <b>Reminder set!</b>\n\n"
            f"⏰ <code>{task['time']} UTC</code>\n"
            f"📝 {text}\n\n"
            f"<i>ID: <code>{task['id']}</code></i>",
            parse_mode="HTML",
        )
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")


# ── /tasks ────────────────────────────────────────────────────────────────────

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    try:
        reminders = sched.get_reminders(update.effective_chat.id)
        if not reminders:
            await update.message.reply_text(
                "📋 No pending reminders.\n\n<i>/remind HH:MM text</i> to add one.",
                parse_mode="HTML",
            )
            return

        lines = ["📋 <b>Pending Reminders:</b>\n"]
        for r in reminders:
            short_id = r['id'].split('_')[-1][:8]   # friendlier ID suffix
            lines.append(
                f"⏰ <code>{r['time']} UTC</code> — {r['text']}\n"
                f"   <i>/cancel <code>{r['id']}</code></i>"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as exc:
        logger.error(f"cmd_tasks error: {exc}", exc_info=True)
        await update.message.reply_text(
            f"❌ Error loading reminders: <code>{exc}</code>", parse_mode="HTML"
        )


# ── /cancel ───────────────────────────────────────────────────────────────────

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
        await update.message.reply_text("❌ Reminder not found.")


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text("<i>Checking status...</i>", parse_mode="HTML")

    ollama_status = _ping_ollama()
    api_key       = os.getenv("ANTHROPIC_API_KEY", "").strip()
    claude_status = "configured ✅" if api_key else "not set ⚠️"
    crypto_key    = os.getenv("CRYPTOCOM_API_KEY", "").strip()
    crypto_status = "configured ✅" if crypto_key else "not set ⚠️"

    await update.message.reply_text(
        f"🦾 <b>ClawBot Status</b> — {_now()}\n\n"
        f"🧠 Ollama:       {ollama_status}\n"
        f"⚡ Claude API:   {claude_status}\n"
        f"📈 Crypto.com:   {crypto_status}\n\n"
        f"<i>Type anything to chat, or use /help for commands.</i>",
        parse_mode="HTML",
    )


# ── /brain ────────────────────────────────────────────────────────────────────

async def cmd_brain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    stats      = get_usage_today()
    api_key    = os.getenv("ANTHROPIC_API_KEY", "").strip()
    ollama_calls  = stats.get("ollama_calls", 0)
    claude_calls  = stats.get("claude_calls", 0)
    in_tok        = stats.get("claude_input_tokens", 0)
    out_tok       = stats.get("claude_output_tokens", 0)
    cache_hits    = stats.get("cache_hits", 0)
    cost          = (in_tok * 0.000001) + (out_tok * 0.000005)
    savings       = cache_hits * 200 * 0.000001

    await update.message.reply_text(
        f"🧠 <b>Brain Stats — Today</b>\n\n"
        f"  Ollama (local/free): {ollama_calls} calls\n"
        f"  Claude Haiku {'✅' if api_key else '⚠️'}: {claude_calls} calls\n"
        f"  Cache hits: {cache_hits} 💾\n"
        f"  Tokens in/out: {in_tok:,} / {out_tok:,}\n"
        f"  Cost today: ${cost:.4f}\n"
        f"  Cache saved: ~${savings:.4f}",
        parse_mode="HTML",
    )


# ── /weather ──────────────────────────────────────────────────────────────────

async def cmd_weather(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /weather [city]")
        return

    location     = " ".join(context.args)
    thinking_msg = await update.message.reply_text(
        f"<i>Looking up {location}...</i>", parse_mode="HTML"
    )
    try:
        import requests as req
        geo = req.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1}, timeout=10,
        )
        geo.raise_for_status()
        results = geo.json().get("results", [])
        if not results:
            raise ValueError(f"Location not found: {location}")

        place   = results[0]
        lat, lon = place["latitude"], place["longitude"]
        name    = place.get("name", location)
        country = place.get("country", "")

        wr = req.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current_weather": True, "timezone": "auto"},
            timeout=10,
        )
        wr.raise_for_status()
        w = wr.json().get("current_weather", {})

        codes = {
            0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 51: "Light drizzle", 61: "Slight rain", 63: "Moderate rain",
            65: "Heavy rain", 71: "Light snow", 80: "Rain showers", 95: "Thunderstorm",
        }
        condition = codes.get(w.get("weathercode"), f"Code {w.get('weathercode')}")

        await thinking_msg.edit_text(
            f"🌦 <b>{name}, {country}</b>\n\n"
            f"Temperature: <code>{w.get('temperature')}°C</code>\n"
            f"Condition:   <code>{condition}</code>\n"
            f"Wind:        <code>{w.get('windspeed')} km/h</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(
            f"🚨 Weather failed: <code>{exc}</code>", parse_mode="HTML"
        )


# ── /selfimprove ─────────────────────────────────────────────────────────────

async def cmd_selfimprove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = [arg for arg in (context.args or []) if arg.strip()]
    if not args:
        await update.message.reply_text(
            "Usage: /selfimprove <init|status|show|log> [args]\n"
            "  /selfimprove init\n"
            "  /selfimprove status\n"
            "  /selfimprove show memory|corrections|heartbeat|index\n"
            "  /selfimprove log correction|memory [text]",
        )
        return

    command = args[0].lower()

    try:
        if command == "init":
            summary = initialize_self_improving()
            await update.message.reply_text(
                "✅ <b>Self-Improving Initialized</b>\n\n"
                f"Path: <code>{summary['path']}</code>\n"
                f"Files: {', '.join(summary['files'])}\n"
                f"Folders: {', '.join(summary['folders'])}",
                parse_mode="HTML",
            )
            return

        if command == "status":
            status = get_status()
            await update.message.reply_text(
                "🧠 <b>Self-Improving Status</b>\n\n"
                f"Path: <code>{status['path']}</code>\n"
                f"Memory lines: <code>{status['memory_lines']}</code>\n"
                f"Correction lines: <code>{status['corrections_lines']}</code>\n"
                f"Projects: <code>{status['projects']}</code>\n"
                f"Domains: <code>{status['domains']}</code>\n"
                f"Archive notes: <code>{status['archive']}</code>\n"
                f"Heartbeat file: <code>{'yes' if status['heartbeat_exists'] else 'no'}</code>",
                parse_mode="HTML",
            )
            return

        if command == "show" and len(args) > 1:
            filename = resolve_file_name(args[1])
            preview = get_file_preview(filename)
            await update.message.reply_text(
                f"📄 Preview: {filename}\n\n{preview}"
            )
            return

        if command == "log" and len(args) > 2:
            target = args[1].lower()
            text   = " ".join(args[2:]).strip()
            if not text:
                raise ValueError("No text provided for log entry.")

            if target == "correction":
                append_correction(text)
                await update.message.reply_text(
                    "✅ Logged correction entry to corrections.md.",
                    parse_mode="HTML",
                )
                return

            if target == "memory":
                append_memory(text)
                await update.message.reply_text(
                    "✅ Logged memory entry to memory.md.",
                    parse_mode="HTML",
                )
                return

            await update.message.reply_text(
                "Usage: /selfimprove log correction|memory [text]",
            )
            return

        await update.message.reply_text(
            "Usage: /selfimprove <init|status|show|log> [args]",
        )
    except FileNotFoundError as exc:
        await update.message.reply_text(f"🚨 File error: {exc}")
    except Exception as exc:
        await update.message.reply_text(f"🚨 Self-improve failed: {exc}")


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🦾 <b>ClawBot Commands</b>\n\n"
        "<b>💡 Business AI (just type or use commands):</b>\n"
        "  [any message]       — chat with Ollama\n"
        "  /ask [question]     — explicit Q&A\n"
        "  /plan [idea]        — structured plan\n"
        "  /research [topic]   — deep research\n"
        "  /clear              — reset memory\n\n"
        "<b>📈 Crypto:</b>\n"
        "  /market              — live prices + analysis\n"
        "  /scan [1h|4h|1d]    — RSI+MACD signals\n"
        "  /dca [asset]         — DCA entry analysis\n"
        "  /autotrade [on|off]  — fully auto daily trading\n\n"
        "<b>💻 PC Execution:</b>\n"
        "  /run [command]      — run shell command\n"
        "  /py [code]          — run Python code\n\n"
        "<b>⏰ Reminders:</b>\n"
        "  /remind HH:MM text  — set daily reminder\n"
        "  /tasks              — list reminders\n"
        "  /cancel [id]        — cancel reminder\n\n"
        "<b>� Self-Improving:</b>\n"
        "  /selfimprove init               — create ~/self-improving/ memory files\n"
        "  /selfimprove status             — show memory and correction counts\n"
        "  /selfimprove show memory|corrections|heartbeat|index\n"
        "  /selfimprove log correction ... — log a correction entry\n"
        "  /selfimprove log memory ...     — log a reusable lesson\n\n"
        "<b>🤖 Multi-Agent Orchestration:</b>\n"
        "  /status             — system health\n"
        "  /brain              — AI usage stats\n"
        "  /weather [city]     — current weather\n"
        "  /stop               — shutdown",
        parse_mode="HTML",
    )


# ── /autotrade ────────────────────────────────────────────────────────────────

async def cmd_autotrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    chat_id = update.effective_chat.id
    arg     = context.args[0].lower() if context.args else ""

    if arg == "on":
        scan_time = context.args[1] if len(context.args) > 1 else "08:00"
        timeframe = context.args[2] if len(context.args) > 2 else "4h"
        cfg = sched.enable_autotrade(chat_id, scan_time=scan_time, timeframe=timeframe)
        await update.message.reply_text(
            f"🤖 <b>Auto-Trade ENABLED</b>\n\n"
            f"⏰ Daily scan: <code>{cfg['scan_time']} UTC</code>\n"
            f"📊 Timeframe: <code>{cfg['timeframe']}</code>\n"
            f"🎯 Executes: HIGH confidence RSI+MACD signals only\n"
            f"💰 Risk: 1.5% of portfolio per trade\n"
            f"🪙 Coins: BTC, SOL, XRP, ETH\n\n"
            f"<i>Use /autotrade off to disable.</i>",
            parse_mode="HTML",
        )

    elif arg == "off":
        sched.disable_autotrade()
        await update.message.reply_text(
            "🤖 <b>Auto-Trade DISABLED</b>\n\nNo more automatic trades. Use /autotrade on to re-enable.",
            parse_mode="HTML",
        )

    elif arg == "now":
        # Manual trigger for testing
        await update.message.reply_text("<i>Running auto-trade scan now...</i>", parse_mode="HTML")
        await sched._run_autotrade()  # type: ignore[attr-defined]

    else:
        cfg    = sched.get_autotrade_status()
        status = "ENABLED ✅" if cfg.get("enabled") else "DISABLED ❌"
        await update.message.reply_text(
            f"🤖 <b>Auto-Trade Status: {status}</b>\n\n"
            f"⏰ Scan time: <code>{cfg.get('scan_time', '08:00')} UTC</code>\n"
            f"📊 Timeframe: <code>{cfg.get('timeframe', '4h')}</code>\n\n"
            f"<b>Commands:</b>\n"
            f"  /autotrade on           — enable (08:00 UTC, 4h)\n"
            f"  /autotrade on 09:00 1d  — custom time + timeframe\n"
            f"  /autotrade off          — disable\n"
            f"  /autotrade now          — run scan immediately",
            parse_mode="HTML",
        )


# ── /news ─────────────────────────────────────────────────────────────────────

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text("<i>Checking macro news calendar...</i>", parse_mode="HTML")
    try:
        from agents.news_filter_agent import check_news_filter, format_telegram_message
        result = check_news_filter()
        msg    = format_telegram_message(result)
        await thinking.edit_text(msg, parse_mode="HTML")
    except Exception as exc:
        await thinking.edit_text(f"🚨 News check failed: <code>{exc}</code>", parse_mode="HTML")


# ── /report ───────────────────────────────────────────────────────────────────

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Analyzing trades + generating AI report...</i>", parse_mode="HTML"
    )
    try:
        from agents.sheets_agent import run_report
        msg, _ = await run_report()
        await thinking.edit_text(msg, parse_mode="HTML")
    except Exception as exc:
        await thinking.edit_text(f"🚨 Report failed: <code>{exc}</code>", parse_mode="HTML")


# ── /backtest ─────────────────────────────────────────────────────────────────

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    # Check if we have saved results already
    from trading.backtest import load_results, format_backtest_message, run_backtest
    arg = context.args[0].lower() if context.args else ""

    if arg == "run":
        thinking = await update.message.reply_text(
            "⏳ <b>Running 4-year backtest on all pairs...</b>\n"
            "<i>Downloading data from Binance + testing 4 strategies. Takes ~2-3 min.</i>",
            parse_mode="HTML",
        )
        try:
            import asyncio
            loop    = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, run_backtest)
            msg     = format_backtest_message(results)
            await thinking.edit_text(msg, parse_mode="HTML")
        except Exception as exc:
            await thinking.edit_text(f"🚨 Backtest failed: <code>{exc}</code>", parse_mode="HTML")
    else:
        # Show saved results or prompt to run
        results = load_results()
        if results:
            msg = format_backtest_message(results)
            await update.message.reply_text(msg + "\n\n<i>Use /backtest run to refresh.</i>", parse_mode="HTML")
        else:
            await update.message.reply_text(
                "📊 <b>No backtest data yet.</b>\n\n"
                "Use <code>/backtest run</code> to download 4 years of Binance data\n"
                "and test all 4 strategies across BTC/ETH/SOL/XRP.\n\n"
                "<i>Takes ~2-3 minutes. Results saved to data/backtest_results.json</i>",
                parse_mode="HTML",
            )


# ── /codereview ───────────────────────────────────────────────────────────────

async def cmd_codereview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id

    # Check for last review summary first
    from agents.code_review_agent import run_code_review, get_last_review_summary
    arg = context.args[0].lower() if context.args else ""

    if arg == "run":
        await update.message.reply_text(
            "🔍 <b>Starting full code review...</b>\n"
            "<i>AI is reading all project files. Takes 2-5 minutes.</i>",
            parse_mode="HTML",
        )
        try:
            msg = await run_code_review(bot=_app.bot, chat_id=chat_id)
        except Exception as exc:
            await update.message.reply_text(
                f"🚨 Code review failed: <code>{exc}</code>", parse_mode="HTML"
            )
    else:
        last = get_last_review_summary()
        if last:
            await update.message.reply_text(
                f"🔍 <b>Last Code Review: {last['date']}</b>\n\n"
                f"<pre>{last['preview']}</pre>\n\n"
                f"Use <code>/codereview run</code> to run a fresh review.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "🔍 <b>No code reviews yet.</b>\n\n"
                "Use <code>/codereview run</code> to start an AI review of all project files.\n"
                "<i>Runs automatically every Sunday 09:00 UTC.</i>",
                parse_mode="HTML",
            )


# ── /orchestrate ─────────────────────────────────────────────────────────────

async def cmd_orchestrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create and manage orchestrated tasks."""
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    orchestrator = get_orchestrator()

    if not args:
        await update.message.reply_text(
            "🤖 <b>Agent Team Orchestration</b>\n\n"
            "Create and manage multi-agent tasks:\n\n"
            "<code>/orchestrate create \"Task Title\" \"Description\"</code> — Create task\n"
            "<code>/orchestrate assign task_id agent_name</code> — Assign to agent\n"
            "<code>/orchestrate start task_id agent_name</code> — Start working\n"
            "<code>/orchestrate review task_id agent_name \"feedback\"</code> — Review task\n"
            "<code>/otasks</code> — List all tasks\n"
            "<code>/otasks pending</code> — Show pending tasks",
            parse_mode="HTML",
        )
        return

    action = args[0].lower()

    if action == "create" and len(args) >= 3:
        title = args[1]
        description = " ".join(args[2:])
        task_id = orchestrator.create_task(title, description)
        await update.message.reply_text(
            f"✅ <b>Task Created</b>\n\n"
            f"<b>ID:</b> <code>{task_id}</code>\n"
            f"<b>Title:</b> {title}\n"
            f"<b>Status:</b> inbox\n\n"
            f"Use <code>/orchestrate assign {task_id} agent_name</code> to assign it.",
            parse_mode="HTML",
        )

    elif action == "assign" and len(args) >= 3:
        task_id = args[1]
        agent_id = args[2]
        if orchestrator.assign_task(task_id, agent_id):
            await update.message.reply_text(
                f"✅ <b>Task Assigned</b>\n\n"
                f"Task <code>{task_id}</code> assigned to <code>{agent_id}</code>\n"
                f"Status: assigned",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ <b>Assignment Failed</b>\n\n"
                f"Could not assign task <code>{task_id}</code> to <code>{agent_id}</code>\n"
                f"Check task exists and is in 'inbox' state.",
                parse_mode="HTML",
            )

    elif action == "start" and len(args) >= 3:
        task_id = args[1]
        agent_id = args[2]
        if orchestrator.start_task(task_id, agent_id):
            await update.message.reply_text(
                f"✅ <b>Task Started</b>\n\n"
                f"Agent <code>{agent_id}</code> started working on task <code>{task_id}</code>\n"
                f"Status: in_progress",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ <b>Start Failed</b>\n\n"
                f"Could not start task <code>{task_id}</code> for agent <code>{agent_id}</code>",
                parse_mode="HTML",
            )

    elif action == "review" and len(args) >= 4:
        task_id = args[1]
        reviewer_id = args[2]
        approved = args[3].lower() in ["approve", "approved", "yes", "pass"]
        feedback = " ".join(args[4:]) if len(args) > 4 else ""

        if orchestrator.review_task(task_id, reviewer_id, approved, feedback):
            status = "approved" if approved else "revision requested"
            await update.message.reply_text(
                f"✅ <b>Review Complete</b>\n\n"
                f"Task <code>{task_id}</code> {status}\n"
                f"Reviewer: <code>{reviewer_id}</code>\n"
                f"Feedback: {feedback or 'None'}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ <b>Review Failed</b>\n\n"
                f"Could not review task <code>{task_id}</code>",
                parse_mode="HTML",
            )

    else:
        await update.message.reply_text(
            "❓ <b>Unknown orchestration command</b>\n\n"
            "Use <code>/orchestrate</code> for help.",
            parse_mode="HTML",
        )


# ── /otasks ───────────────────────────────────────────────────────────────────

async def cmd_otasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List orchestration tasks."""
    if not is_authorized(update.effective_chat.id):
        return

    orchestrator = get_orchestrator()
    args = context.args

    if args and args[0].lower() == "pending":
        tasks = orchestrator.get_pending_tasks()
        filter_desc = "pending"
    else:
        tasks = list(orchestrator.tasks.values())
        filter_desc = "all"

    if not tasks:
        await update.message.reply_text(
            f"📋 <b>No {filter_desc} tasks</b>\n\n"
            "Use <code>/orchestrate create \"Title\" \"Description\"</code> to create one.",
            parse_mode="HTML",
        )
        return

    # Sort by creation time, newest first
    tasks.sort(key=lambda t: t.created_at, reverse=True)

    msg = f"📋 <b>{filter_desc.title()} Tasks ({len(tasks)})</b>\n\n"
    for task in tasks[:10]:  # Limit to 10 most recent
        status_emoji = {
            "inbox": "📥",
            "assigned": "👤",
            "in_progress": "⚡",
            "review": "🔍",
            "revision": "🔄",
            "done": "✅"
        }.get(task.state, "❓")

        assigned = f" → {task.assigned_to}" if task.assigned_to else ""
        msg += f"{status_emoji} <code>{task.id}</code>{assigned}\n"
        msg += f"   <b>{task.title}</b>\n"
        msg += f"   {task.state.replace('_', ' ')} • {task.created_at[:16]}\n\n"

    if len(tasks) > 10:
        msg += f"<i>Showing 10 of {len(tasks)} tasks. Use filters for more.</i>"

    await update.message.reply_text(msg, parse_mode="HTML")


# ── /upgrade ──────────────────────────────────────────────────────────────────

async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /upgrade          — dry run: show what would be fixed
    /upgrade apply    — generate + apply fixes from latest code review, then restart
    /upgrade review   — trigger fresh code review then auto-fix
    """
    if not is_authorized(update.effective_chat.id):
        return

    arg = context.args[0].lower() if context.args else ""
    chat_id = update.effective_chat.id

    if arg == "review":
        # Fire-and-forget: run code review in background so bot stays responsive
        await update.message.reply_text(
            "🔍 <b>Running code review in background...</b>\n"
            "<i>Bot stays responsive. You'll get a message when done.</i>",
            parse_mode="HTML",
        )

        async def _review_then_upgrade():
            try:
                from agents.code_review_agent import run_code_review
                await run_code_review(update.get_bot(), chat_id)
            except Exception as e:
                await update.get_bot().send_message(
                    chat_id, f"⚠️ Code review failed: <code>{e}</code>", parse_mode="HTML"
                )
        asyncio.create_task(_review_then_upgrade())
        return   # return immediately — bot stays responsive

    dry_run = arg != "apply"
    mode = "DRY RUN" if dry_run else "LIVE"
    await update.message.reply_text(
        f"🤖 <b>Auto-Upgrade [{mode}]</b>\n<i>Analyzing latest code review...</i>",
        parse_mode="HTML",
    )

    try:
        from agents.auto_upgrade import run_auto_upgrade, format_upgrade_message
        summary = run_auto_upgrade(dry_run=dry_run)
        msg = format_upgrade_message(summary)
        await update.message.reply_text(msg, parse_mode="HTML")

        # Auto-restart after live fixes
        if not dry_run and summary.get("fixes_applied", 0) > 0:
            await update.message.reply_text(
                "🔄 <b>Restarting to apply fixes...</b>",
                parse_mode="HTML",
            )
            import sys
            python = sys.executable
            os.execv(python, [python, "-m", "content.receiver"])

    except Exception as exc:
        await update.message.reply_text(
            f"❌ <b>Upgrade failed:</b> <code>{exc}</code>",
            parse_mode="HTML",
        )


# ── /restart ──────────────────────────────────────────────────────────────────

async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart the bot process in-place, killing any duplicate instances first."""
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "🔄 <b>ClawBot restarting...</b>\n<i>Back in a few seconds.</i>",
        parse_mode="HTML",
    )
    import sys
    my_pid = os.getpid()

    # Kill ALL other python processes (duplicate bot instances) via PowerShell
    try:
        subprocess.run(
            [
                "powershell.exe", "-Command",
                f"Get-Process python -ErrorAction SilentlyContinue | "
                f"Where-Object {{$_.Id -ne {my_pid}}} | Stop-Process -Force",
            ],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    # Small delay so Telegram sees us disconnect before reconnecting
    import time
    time.sleep(2)

    python = sys.executable
    os.execv(python, [python, "-m", "content.receiver"])


# ── /stop ─────────────────────────────────────────────────────────────────────

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "👋 <b>ClawBot shutting down.</b> See you next time!", parse_mode="HTML"
    )
    os.kill(os.getpid(), signal.SIGINT)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    sched.set_send_fn(_scheduler_send)
    sched.start_scheduler()
    sched.reload_autotrade()   # re-register daily job if it was enabled before restart

    _app = Application.builder().token(token).build()

    # Wire up automatic scheduled agents (news + code review)
    # These run in the background — news every 15 min, code review every Sunday 09:00 UTC
    owner_chat_id = int(os.getenv("ALLOWED_CHAT_ID", "0").split(",")[0].strip() or "0")
    if owner_chat_id:
        _raw_scheduler = sched.get_scheduler()
        if _raw_scheduler:
            try:
                from agents.news_filter_agent import check_and_alert as _news_alert
                _raw_scheduler.add_job(
                    _news_alert, "interval", minutes=15,
                    id="news_filter", replace_existing=True,
                    kwargs={"bot": _app.bot, "chat_id": owner_chat_id},
                )
                print("✅ News filter job registered (every 15 min)")
            except Exception as _e:
                print(f"⚠️  News alert job not started: {_e}")
            try:
                from agents.code_review_agent import schedule_weekly_review
                schedule_weekly_review(_raw_scheduler, _app.bot, owner_chat_id)
                print("✅ Weekly code review job registered (Sunday 09:00 UTC)")
            except Exception as _e:
                print(f"⚠️  Code review job not started: {_e}")

    # Commands
    _app.add_handler(CommandHandler("start",    cmd_start))
    _app.add_handler(CommandHandler("help",     cmd_help))   # alias — was referenced but not registered
    _app.add_handler(CommandHandler("ask",      cmd_ask))
    _app.add_handler(CommandHandler("plan",     cmd_plan))
    _app.add_handler(CommandHandler("research", cmd_research))
    _app.add_handler(CommandHandler("clear",    cmd_clear))
    _app.add_handler(CommandHandler("market",   cmd_market))
    _app.add_handler(CommandHandler("scan",     cmd_scan))
    _app.add_handler(CommandHandler("dca",      cmd_dca))
    _app.add_handler(CommandHandler("run",      cmd_run))
    _app.add_handler(CommandHandler("py",       cmd_py))
    _app.add_handler(CommandHandler("remind",   cmd_remind))
    _app.add_handler(CommandHandler("tasks",    cmd_tasks))
    _app.add_handler(CommandHandler("cancel",   cmd_cancel))
    _app.add_handler(CommandHandler("status",   cmd_status))
    _app.add_handler(CommandHandler("brain",    cmd_brain))
    _app.add_handler(CommandHandler("weather",  cmd_weather))
    _app.add_handler(CommandHandler("help",     cmd_help))
    _app.add_handler(CommandHandler("autotrade",  cmd_autotrade))
    _app.add_handler(CommandHandler("save",       cmd_save))
    _app.add_handler(CommandHandler("notes",      cmd_notes))
    _app.add_handler(CommandHandler("news",       cmd_news))
    _app.add_handler(CommandHandler("report",     cmd_report))
    _app.add_handler(CommandHandler("backtest",   cmd_backtest))
    _app.add_handler(CommandHandler("codereview", cmd_codereview))
    _app.add_handler(CommandHandler("orchestrate", cmd_orchestrate))
    _app.add_handler(CommandHandler("otasks",     cmd_otasks))
    _app.add_handler(CommandHandler("selfimprove", cmd_selfimprove))
    _app.add_handler(CommandHandler("upgrade",    cmd_upgrade))
    _app.add_handler(CommandHandler("restart",    cmd_restart))
    _app.add_handler(CommandHandler("stop",       cmd_stop))

    # Free-text conversation (must be last — catches all non-command text)
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🦾 ClawBot v0.6 running.")
    print("   Chat freely or use commands. /help for the full list.")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
