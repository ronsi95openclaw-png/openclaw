"""ClawBot v0.9 — Business AI Partner + Trading Bot (Version: core.__version__)

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
    /remind HH:MM text — set a one-time reminder for the next HH:MM UTC
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
import html
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

# Inject FFMPEG_PATH into PATH if set in .env so yt-dlp + ffmpeg work
_ffmpeg_bin = os.getenv("FFMPEG_PATH", "").strip()
if _ffmpeg_bin and _ffmpeg_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

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
from security import audit
from security.blocklist import is_blocked
from skills.agent_team_orchestrator import get_orchestrator
from skills.self_improving import (
    append_correction,
    append_memory,
    get_file_preview,
    get_status,
    initialize_self_improving,
    resolve_file_name,
)
from skills.second_brain import (
    append_log_entry as append_second_brain_log,
    create_raw_source_file,
    get_file_preview as get_second_brain_file_preview,
    get_second_brain_status,
    initialize_second_brain as initialize_second_brain_vault,
    list_raw_sources,
    list_wiki_pages,
    resolve_second_brain_file_name,
)
from agents.lifeos_agent import (
    add_score,
    get_dashboard_data,
    get_scores,
    load_intake,
    log_expense,
    log_income,
    log_weight,
    save_intake,
)
from agents.lifeos_checkin import (
    clear_state,
    get_pending_state,
    handle_checkin_reply,
    start_evening_checkin,
    start_morning_checkin,
)

_LOG_DIR = Path(__file__).parent.parent / "data" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("openclaw.receiver")


# ── Telegram helpers ──────────────────────────────────────────────────────────

import re as _re

def _safe_html(text: str) -> str:
    """Escape content so it is safe for Telegram HTML parse mode.

    Strategy:
      1. html.escape() the entire string (converts all & < > to entities).
      2. Re-allow only the tags Telegram actually supports.

    This correctly handles <20%>, <br>, unclosed < characters, and any
    other invalid markup that triggers "Can't parse entities" from Telegram.

    Telegram supports: b, i, u, s, em, strong, code, pre, a, tg-spoiler.
    """
    import html as _html_mod
    # Step 1: escape everything so Telegram sees no raw angle brackets
    escaped = _html_mod.escape(str(text))
    # Step 2: restore only valid Telegram HTML tags.
    # After html.escape, <b> becomes &lt;b&gt; — we undo that for safe tags.
    ALLOWED_TAGS = r'/?(?:b|i|u|s|em|strong|code|pre|tg-spoiler)(?:\s[^&]*?)?' 
    def _restore(m):
        inner = m.group(1)
        if _re.fullmatch(ALLOWED_TAGS, inner.strip(), _re.IGNORECASE):
            return f'<{inner}>'
        return m.group(0)  # leave as &lt;...&gt;
    return _re.sub(r'&lt;([^&]+?)&gt;', _restore, escaped)


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
        model = os.getenv("OLLAMA_MODEL", "gemma3")
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
        "  /dca [asset]        — DCA entry analysis\n"
        "  /trades [n]         — last N trade decisions\n\n"
        "<b>💻 PC Execution:</b>\n"
        "  /run [command]      — run shell command\n"
        "  /py [code]          — run Python code\n\n"
        "<b>⏰ Reminders:</b>\n"
        "  /remind HH:MM text  — set one-time reminder\n"
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
        "  /selfimprove log memory ...     — log a reusable lesson\n"
        "  /secondbrain init               — create a second-brain Obsidian vault\n"
        "  /secondbrain status             — show second-brain vault status\n"
        "  /secondbrain show index|log|CLAUDE.md\n"
        "  /secondbrain newsource title | text — add a raw source file\n\n"
        "<b>🏆 LifeOS:</b>\n"
        "  /lifeos            — dashboard summary\n"
        "  /morning           — morning check-in\n"
        "  /evening           — evening check-in\n"
        "  /score             — points + streak\n"
        "  /logweight [kg]    — log weight\n"
        "  /logexpense [amt] [cat] — log expense\n"
        "  /lifesetup         — configure profile\n"
        "  /lifemode [mode]   — STRICT/BALANCED/CHILL\n"
        "  /dash              — full mobile dashboard\n\n"
        "<b>⚙️ System:</b>\n"
        "  /mode               — check trading mode\n"
        "  /live               — switch to LIVE trading\n"
        "  /demo               — switch to DEMO mode\n"
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

    # ── LifeOS check-in intercept ─────────────────────────────────────────────
    if get_pending_state(update.effective_chat.id):
        done, reply = handle_checkin_reply(update.effective_chat.id, update.message.text or "")
        await _safe_reply(update.message, reply)
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
            f"🦾 <b>ClawBot</b> <i>({brain})</i>\n\n{html.escape(response)}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


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
            f"🦾 <b>ClawBot</b> <i>({brain})</i>\n\n{html.escape(response)}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


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
            f"📋 <b>Plan: {html.escape(idea[:40])}</b>\n\n{html.escape(response)}", parse_mode="HTML"
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


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
        await thinking_msg.edit_text(
            f"🔬 <b>Research: {html.escape(topic[:40])}</b>\n\n{html.escape(response)}", parse_mode="HTML"
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


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
            f"📈 <b>DCA: {html.escape(asset)}</b>\n\n{html.escape(price_context)}{html.escape(response)}", parse_mode="HTML"
        )
    except Exception as exc:
        await thinking_msg.edit_text(f"🚨 Error: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")


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
    actor = str(update.effective_chat.id)

    # Blocklist check BEFORE we run anything.
    hit = is_blocked(command)
    if hit:
        audit.log_command(actor, command, source="run", outcome="blocked")
        await update.message.reply_text(
            f"⛔ Command rejected: matches blocklist pattern <code>{hit}</code>",
            parse_mode="HTML",
        )
        return

    audit.log_command(actor, command, source="run", outcome="allowed")

    thinking_msg = await update.message.reply_text(
        f"<i>Running:</i> <code>{html.escape(command)}</code>", parse_mode="HTML"
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

    loop   = asyncio.get_running_loop()
    output, rc = await loop.run_in_executor(None, _execute)

    # Truncate if too long for Telegram (4096 char limit)
    if len(output) > 3500:
        output = output[:3500] + "\n... (truncated)"

    status = "✅" if rc == 0 else "❌"
    await thinking_msg.edit_text(
        f"{status} <b>/run</b> <code>{html.escape(command)}</code>\n\n<pre>{html.escape(output)}</pre>",
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
    actor = str(update.effective_chat.id)

    # Blocklist check BEFORE we run anything.
    hit = is_blocked(code)
    if hit:
        audit.log_command(actor, code, source="py", outcome="blocked")
        await update.message.reply_text(
            f"⛔ Code rejected: matches blocklist pattern <code>{hit}</code>",
            parse_mode="HTML",
        )
        return

    audit.log_command(actor, code, source="py", outcome="allowed")

    thinking_msg = await update.message.reply_text(
        f"<i>Running Python:</i> <code>{html.escape(code)}</code>", parse_mode="HTML"
    )

    def _execute():
        try:
            import sys as _sys
            python = _sys.executable  # use the same interpreter running the bot
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

    loop   = asyncio.get_running_loop()
    output, rc = await loop.run_in_executor(None, _execute)

    if len(output) > 3500:
        output = output[:3500] + "\n... (truncated)"

    status = "✅" if rc == 0 else "❌"
    await thinking_msg.edit_text(
        f"{status} <b>/py</b>\n<code>{html.escape(code)}</code>\n\n<pre>{html.escape(output)}</pre>",
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


# ── LifeOS ─────────────────────────────────────────────────────────────────────

async def cmd_lifeos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show LifeOS status summary."""
    if not is_authorized(update.effective_chat.id):
        return
    intake = load_intake()
    if not intake:
        await _safe_reply(update.message,
            "LifeOS is not set up yet.\n\nRun /lifesetup to enter your profile.")
        return
    try:
        data = get_dashboard_data()
    except Exception:
        await _safe_reply(update.message, "⚠️ Could not load LifeOS data. Check bot logs.")
        return
    f = data["fitness"]
    fin = data["finance"]
    h = data["habits"]
    lines = [
        "<b>LifeOS Dashboard</b>\n",
        "<b>Fitness</b>",
        f"  Weight: {f['weight']} kg  →  Goal: {f['goal_weight']} kg",
        f"  Workouts this week: {f['workouts']}",
        "",
        "<b>Finance</b>",
        f"  Monthly income: ${fin['income']}",
        f"  Debt: ${fin['debt']}",
        f"  Today's expenses: ${fin['expenses']:.2f}",
        "",
        "<b>Habits</b>",
        f"  Score: {h['score']} pts  |  Streak: {h['streak']} days",
        f"  Completion rate (7d): {h['completionRate']}%",
        "",
        f"Coach mode: {data['profile']['coach_mode']}",
        "",
        "/morning — start morning check-in",
        "/evening — start evening check-in",
        "/score   — gamification leaderboard",
    ]
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the morning check-in flow."""
    if not is_authorized(update.effective_chat.id):
        return
    first_q = start_morning_checkin(update.effective_chat.id)
    await _safe_reply(update.message, f"<b>Morning Check-in</b>\n\n{first_q}")


async def cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the evening check-in flow."""
    if not is_authorized(update.effective_chat.id):
        return
    first_q = start_evening_checkin(update.effective_chat.id)
    await _safe_reply(update.message, f"<b>Evening Check-in</b>\n\n{first_q}")


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show gamification score + streak."""
    if not is_authorized(update.effective_chat.id):
        return
    s = get_scores()
    streak_bar = "🔥" * min(s["streak"], 14)
    lines = [
        "<b>LifeOS Score</b>\n",
        f"Total points:  <b>{s['total']}</b>",
        f"Current streak: <b>{s['streak']} days</b>  {streak_bar}",
        "",
        "<b>Points table</b>",
        "  +10  workout completed",
        "  +10  diet adherence",
        "  +15  deep work session",
        "  +5   expense tracked",
        "  -10  missed workout",
        "  -10  overspending",
        "  -15  skipped priorities",
    ]
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_logweight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/logweight 84.5 — log today's weight in kg."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await _safe_reply(update.message, "Usage: /logweight [kg]\nExample: /logweight 84.5")
        return
    try:
        kg = float(context.args[0])
    except ValueError:
        await _safe_reply(update.message, "Invalid number. Example: /logweight 84.5")
        return
    if kg <= 0:
        await _safe_reply(update.message, "Weight must be a positive number.")
        return
    log_weight(kg)
    intake = load_intake()
    goal = intake.get("goal_weight", "?")
    diff = round(kg - float(goal), 1) if goal != "?" else "?"
    await _safe_reply(update.message,
        f"Weight logged: <b>{kg} kg</b>\nGoal: {goal} kg  |  Gap: {diff} kg")


async def cmd_logexpense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/logexpense 12.50 food lunch — log an expense."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args or len(context.args) < 2:
        await _safe_reply(update.message,
            "Usage: /logexpense [amount] [category] [description]\n"
            "Example: /logexpense 12.50 food lunch")
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await _safe_reply(update.message, "Invalid amount. Example: /logexpense 12.50 food lunch")
        return
    if amount <= 0:
        await _safe_reply(update.message, "Amount must be a positive number.")
        return
    category    = context.args[1]
    description = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    log_expense(amount, category, description)
    add_score("expense_tracked")
    await _safe_reply(update.message,
        f"Expense logged: <b>${amount:.2f}</b> — {category}"
        + (f" ({description})" if description else "")
        + "\n+5 pts")


async def cmd_logincome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log_income 150 freelance website job — log income earned."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args or len(context.args) < 2:
        await _safe_reply(update.message,
            "Usage: /log_income [amount] [source] [description]\n"
            "Example: /log_income 150 freelance website job\n"
            "Example: /log_income 50 crypto BTC sell profit")
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await _safe_reply(update.message, "Invalid amount. Example: /log_income 150 freelance")
        return
    if amount <= 0:
        await _safe_reply(update.message, "Amount must be a positive number.")
        return
    source      = context.args[1]
    description = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    log_income(amount, source, description)
    add_score("deep_work", +15)  # income = productive work, reward it
    intake = load_intake()
    monthly = intake.get("monthly_income", 0)
    await _safe_reply(update.message,
        f"Income logged: <b>${amount:.2f}</b> — {source}"
        + (f" ({description})" if description else "")
        + f"\n+15 pts\n\n<i>Monthly target: ${monthly}</i>")


async def cmd_lifesetup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifesetup key=value — save intake profile fields."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await _safe_reply(update.message,
            "<b>LifeOS Setup</b>\n\n"
            "Use: /lifesetup key=value [key=value ...]\n\n"
            "Available keys:\n"
            "  weight=85           current weight (kg)\n"
            "  goal_weight=75      target weight (kg)\n"
            "  monthly_income=5000\n"
            "  total_debt=15000\n"
            "  investments=2000\n"
            "  coach_mode=STRICT   (STRICT / BALANCED / CHILL)\n\n"
            "Example:\n"
            "/lifesetup weight=85 goal_weight=75 monthly_income=5000")
        return
    profile: dict = {}
    errors: list = []
    for arg in context.args:
        if "=" not in arg:
            errors.append(f"Skipped '{arg}' (no '=' found)")
            continue
        key, _, val = arg.partition("=")
        key = key.strip()
        val = val.strip()
        if key in ("weight", "goal_weight", "monthly_income", "total_debt", "investments"):
            try:
                profile[key] = float(val)
            except ValueError:
                errors.append(f"'{key}' must be a number, got '{val}'")
                continue
            if profile[key] < 0:
                errors.append(f"'{key}' must be non-negative, got '{val}'")
                del profile[key]
                continue
        else:
            profile[key] = val
    if profile:
        save_intake(profile)
    lines = ["<b>LifeOS profile updated:</b>"]
    for k, v in profile.items():
        lines.append(f"  {k} = {v}")
    if errors:
        lines.append("\n<b>Warnings:</b>")
        lines.extend(f"  {e}" for e in errors)
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_lifemode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifemode strict|balanced|chill — change coach personality."""
    if not is_authorized(update.effective_chat.id):
        return
    valid = {"strict": "STRICT", "balanced": "BALANCED", "chill": "CHILL"}
    if not context.args or context.args[0].lower() not in valid:
        await _safe_reply(update.message, "Usage: /lifemode strict|balanced|chill")
        return
    mode = valid[context.args[0].lower()]
    save_intake({"coach_mode": mode})
    await _safe_reply(update.message, f"Coach mode set to <b>{mode}</b>.")


async def cmd_lifeschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifeschedule on 07:00 20:00 | off — configure auto check-ins (UTC)."""
    if not is_authorized(update.effective_chat.id):
        return
    from core.scheduler import enable_lifeos_schedule, disable_lifeos_schedule
    if not context.args:
        await _safe_reply(update.message,
            "Usage:\n"
            "  /lifeschedule on [morning_UTC] [evening_UTC]\n"
            "  /lifeschedule off\n\n"
            "Example: /lifeschedule on 07:00 20:00")
        return
    if context.args[0].lower() == "off":
        disable_lifeos_schedule()
        await _safe_reply(update.message, "LifeOS scheduled check-ins disabled.")
        return
    elif context.args[0].lower() == "on":
        morning = context.args[1] if len(context.args) > 1 else "07:00"
        evening = context.args[2] if len(context.args) > 2 else "20:00"
        # validate HH:MM format
        import re as _re_sched
        if not (_re_sched.match(r'^\d{2}:\d{2}$', morning) and _re_sched.match(r'^\d{2}:\d{2}$', evening)):
            await _safe_reply(update.message, "Time must be HH:MM format. Example: /lifeschedule on 07:00 20:00")
            return
        cfg = enable_lifeos_schedule(update.effective_chat.id, morning, evening)
        await _safe_reply(update.message,
            f"LifeOS check-ins scheduled:\n"
            f"  Morning: {cfg['morning_time']} UTC\n"
            f"  Evening: {cfg['evening_time']} UTC")
    else:
        await _safe_reply(update.message,
            "Usage:\n"
            "  /lifeschedule on [morning_UTC] [evening_UTC]\n"
            "  /lifeschedule off\n\n"
            "Example: /lifeschedule on 07:00 20:00")


async def cmd_dash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dash — full bot dashboard summary for mobile use."""
    if not is_authorized(update.effective_chat.id):
        return
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Market prices ──────────────────────────────────────────────────────
    price_lines = []
    try:
        from core.market import _fetch_prices
        raw = _fetch_prices()
        labels = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
        prices = {labels[k]: raw[k]["usd"] for k in labels if k in raw}
        for coin, price in list(prices.items())[:4]:
            price_lines.append(f"  {coin}: ${price:,.0f}")
    except Exception:
        price_lines.append("  (prices unavailable)")

    # ── Portfolio ──────────────────────────────────────────────────────────
    portfolio_line = ""
    try:
        from trading.exchange import get_account_balance, get_portfolio_value_usd
        bal = get_account_balance()
        total = get_portfolio_value_usd(bal)
        portfolio_line = f"Portfolio: ~${total:,.2f}"
    except Exception:
        portfolio_line = "Portfolio: (unavailable)"

    # ── LifeOS ─────────────────────────────────────────────────────────────
    life_lines = []
    try:
        life = get_dashboard_data()
        h = life["habits"]
        f = life["fitness"]
        fin = life["finance"]
        streak_bar = "🔥" * min(h["streak"], 7)
        life_lines = [
            f"  Score: {h['score']} pts  |  Streak: {h['streak']}d {streak_bar}",
            f"  Weight: {f['weight']} kg → {f['goal_weight']} kg",
            f"  Today spend: ${fin['expenses']:.2f}",
            f"  Workouts (7d): {f['workouts']}",
        ]
    except Exception:
        life_lines = ["  (LifeOS not set up — run /lifesetup)"]

    # ── Autotrade ──────────────────────────────────────────────────────────
    autotrade_line = ""
    try:
        from core.scheduler import get_autotrade_status
        at = get_autotrade_status()
        autotrade_line = f"Autotrade: {'ON ✅' if at.get('enabled') else 'OFF'}"
    except Exception:
        autotrade_line = "Autotrade: (unavailable)"

    # ── AI usage ───────────────────────────────────────────────────────────
    brain_line = ""
    try:
        usage = get_usage_today()
        ollama = usage.get('ollama_calls', 0)
        claude = usage.get('claude_calls', 0)
        cached = usage.get('cache_hits', 0)
        brain_line = f"Brain: {ollama} Ollama / {claude} Claude / {cached} cached"
    except Exception:
        brain_line = ""

    lines = [
        f"<b>ClawBot Dashboard</b> — {ts}\n",
        f"<b>Markets</b>",
    ] + price_lines + [
        "",
        portfolio_line,
        autotrade_line,
        "",
        "<b>LifeOS</b>",
    ] + life_lines + [
        "",
        brain_line,
        "",
        "/morning /evening /score /lifeos",
    ]
    await _safe_reply(update.message, "\n".join(lines))


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


# ── /trades ─────────────────────────────────────────────────────────────────────────────

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent trade history from trades.log.

    Usage:
        /trades       -- last 10 trades
        /trades N     -- last N trades (max 50)
        /trades all   -- last 20 trades
    """
    if not is_authorized(update.effective_chat.id):
        return

    # Determine how many trades to show
    arg = context.args[0].lower() if context.args else ""
    if arg == "all":
        n = 20
        label = "last 20"
    elif arg.isdigit():
        n = max(1, min(int(arg), 50))
        label = f"last {n}"
    else:
        n = 10
        label = "last 10"

    trade_log = Path(__file__).parent.parent / "data" / "logs" / "trades.log"

    if not trade_log.exists() or trade_log.stat().st_size == 0:
        await update.message.reply_text(
            "📭 <b>No trades logged yet.</b>\n\n"
            "Enable auto-trading: <code>/autotrade on</code>",
            parse_mode="HTML",
        )
        return

    # Read last N non-empty lines
    with open(trade_log, "r", encoding="utf-8") as _tf:
        _all_lines = [ln.strip() for ln in _tf.readlines() if ln.strip()]
    lines = _all_lines[-n:]

    if not lines:
        await update.message.reply_text(
            "📭 <b>No trades logged yet.</b>\n\n"
            "Enable auto-trading: <code>/autotrade on</code>",
            parse_mode="HTML",
        )
        return

    # Try to parse each line as JSON
    import json as _json
    rows = []
    parse_ok = True
    for _raw in lines:
        try:
            rows.append(_json.loads(_raw))
        except Exception:
            parse_ok = False
            break

    if not parse_ok:
        # Fall back to raw monospace display
        raw_text = "\n".join(lines)
        await update.message.reply_text(
            f"📊 <b>Recent Trades ({label})</b>\n\n"
            f"<pre>{_safe_html(raw_text)}</pre>",
            parse_mode="HTML",
        )
        return

    # Format parsed JSON trades
    import datetime as _dt
    net_pnl = 0.0
    net_has_pnl = False
    formatted_rows = []
    for _t in rows:
        _symbol  = str(_t.get("symbol", _t.get("pair", _t.get("coin", "?")))).replace("/", "_")
        _side    = str(_t.get("side", _t.get("action", "?"))).upper()
        _price   = _t.get("price", _t.get("fill_price", _t.get("entry_price", 0)))
        _pnl     = _t.get("pnl", _t.get("pnl_pct", _t.get("profit", None)))
        _ts_raw  = _t.get("timestamp", _t.get("time", _t.get("date", "")))

        try:
            _price_str = f"${float(_price):,.0f}"
        except Exception:
            _price_str = str(_price)

        if _pnl is not None:
            try:
                _pnl_f = float(_pnl)
                net_pnl += _pnl_f
                net_has_pnl = True
                _pnl_icon = "✅" if _pnl_f >= 0 else "❌"
                _pnl_str  = f"{_pnl_icon} {_pnl_f:+.2f}%"
            except Exception:
                _pnl_str = str(_pnl)
        else:
            _pnl_str = "—"

        try:
            if isinstance(_ts_raw, (int, float)):
                _ts_f = float(_ts_raw)
                if _ts_f > 1e10:
                    _ts_f /= 1000
                _ts_str = _dt.datetime.utcfromtimestamp(_ts_f).strftime("%Y-%m-%d %H:%M")
            else:
                _ts_str = str(_ts_raw)[:16]
        except Exception:
            _ts_str = str(_ts_raw)[:16]

        formatted_rows.append(f"{_symbol:<12} {_side:<4} {_price_str:<10} {_pnl_str:<14} {_ts_str}")

    trades_block = "\n".join(formatted_rows)
    net_str = f"{net_pnl:+.2f}%" if net_has_pnl else "N/A"

    await update.message.reply_text(
        f"📊 <b>Recent Trades ({label})</b>\n\n"
        f"<pre>{trades_block}</pre>\n\n"
        f"Total: {len(rows)} trades · Net: {net_str}",
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


# ── /secondbrain ─────────────────────────────────────────────────────────────

async def cmd_secondbrain(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = [arg.strip() for arg in (context.args or []) if arg.strip()]
    command = args[0].lower() if args else ""

    _HELP = (
        "🧠 <b>Second Brain</b> — Obsidian AI vault\n\n"
        "<b>Setup:</b>\n"
        "  /secondbrain init — create vault structure\n"
        "  /secondbrain status — vault health\n"
        "  /secondbrain map — navigate all pages + sources\n\n"
        "<b>Add knowledge:</b>\n"
        "  /secondbrain newsource title | text\n"
        "  /secondbrain create title | content\n\n"
        "<b>AI workflows:</b>\n"
        "  /secondbrain ingest — AI reads sources → generates wiki pages\n"
        "  /secondbrain query your question here\n"
        "  /secondbrain query [save] your question — saves answer as wiki page\n"
        "  /secondbrain health — AI audits wiki for contradictions + orphans\n\n"
        "<b>Browse:</b>\n"
        "  /secondbrain show index|log|CLAUDE.md|page_name\n"
        "  /secondbrain pages — list all wiki pages\n"
        "  /secondbrain sources — list all raw sources\n"
        "  /secondbrain find keyword — search across wiki\n\n"
        "<b>Edit:</b>\n"
        "  /secondbrain log action | note\n"
        "  /secondbrain delete page_name\n\n"
        "<b>System sync:</b>\n"
        "  /secondbrain sync — re-ingest ALL OpenClaw knowledge (wiki, agents, architecture)"
    )

    if not command:
        await update.message.reply_text(_HELP, parse_mode="HTML")
        return

    try:
        # ── INIT ──────────────────────────────────────────────────────────────
        if command == "init":
            from skills.second_brain import initialize_second_brain, bootstrap_openclaw
            summary = initialize_second_brain()
            # Auto-seed with OpenClaw system knowledge on first init
            boot = bootstrap_openclaw()
            await update.message.reply_text(
                "✅ <b>Second Brain Initialized</b>\n\n"
                f"Vault: <code>{summary['path']}</code>\n"
                f"raw-sources/ · wiki/ · CLAUDE.md · index.md · log.md\n\n"
                f"Auto-seeded with <b>{boot['sources_created']} OpenClaw sources</b>.\n"
                f"Run /secondbrain ingest to generate wiki pages now.",
                parse_mode="HTML",
            )

        # ── STATUS ────────────────────────────────────────────────────────────
        elif command == "status":
            status = get_second_brain_status()
            await update.message.reply_text(
                "🧠 <b>Second Brain Status</b>\n\n"
                f"Vault: <code>{status['path']}</code>\n"
                f"Initialized: {'✅' if status['exists'] else '❌'}\n"
                f"Raw sources: <code>{status['raw_source_count']}</code>\n"
                f"Wiki pages: <code>{status['wiki_page_count']}</code>\n"
                f"Schema: {'✅' if status['schema_exists'] else '❌'} · "
                f"Index: {'✅' if status['index_exists'] else '❌'} · "
                f"Log: {'✅' if status['log_exists'] else '❌'}",
                parse_mode="HTML",
            )

        # ── MAP ───────────────────────────────────────────────────────────────
        elif command == "map":
            from skills.second_brain import get_full_wiki_summary
            summary = get_full_wiki_summary()
            await update.message.reply_text(summary, parse_mode="HTML")

        # ── PAGES ─────────────────────────────────────────────────────────────
        elif command == "pages":
            from skills.second_brain import list_wiki_pages
            pages = list_wiki_pages()
            if not pages:
                await update.message.reply_text("📂 No wiki pages yet. Run /secondbrain ingest to generate them.")
            else:
                lines = "\n".join(f"  • [[{p.replace('.md','')}]]" for p in pages)
                await update.message.reply_text(f"📚 <b>Wiki Pages ({len(pages)})</b>\n\n{lines}", parse_mode="HTML")

        # ── SOURCES ───────────────────────────────────────────────────────────
        elif command == "sources":
            from skills.second_brain import list_raw_sources
            sources = list_raw_sources()
            if not sources:
                await update.message.reply_text("📂 No raw sources yet. Use /secondbrain newsource to add.")
            else:
                lines = "\n".join(f"  • {s}" for s in sources)
                await update.message.reply_text(f"📁 <b>Raw Sources ({len(sources)})</b>\n\n{lines}", parse_mode="HTML")

        # ── SHOW ──────────────────────────────────────────────────────────────
        elif command == "show" and len(args) > 1:
            filename = resolve_second_brain_file_name(args[1])
            preview = get_second_brain_file_preview(filename)
            await update.message.reply_text(f"📄 <b>{filename}</b>\n\n<pre>{preview}</pre>", parse_mode="HTML")

        # ── FIND ──────────────────────────────────────────────────────────────
        elif command == "find" and len(args) > 1:
            from skills.second_brain import search_wiki
            query = " ".join(args[1:])
            results = search_wiki(query)
            if not results:
                await update.message.reply_text(f"🔍 No results for: <code>{query}</code>", parse_mode="HTML")
            else:
                lines = []
                for r in results[:8]:
                    page = r["page"].replace(".md", "")
                    hits = " · ".join(r["matches"][:2])[:100]
                    lines.append(f"[[{page}]]\n  ↳ {hits}")
                await update.message.reply_text(
                    f"🔍 <b>Results for '{query}' ({len(results)})</b>\n\n" + "\n\n".join(lines),
                    parse_mode="HTML",
                )

        # ── NEWSOURCE ─────────────────────────────────────────────────────────
        elif command == "newsource" and len(args) > 1:
            payload = " ".join(args[1:])
            if "|" not in payload:
                raise ValueError("Use: /secondbrain newsource title | text")
            title, body = [p.strip() for p in payload.split("|", 1)]
            path = create_raw_source_file(title, body)
            await update.message.reply_text(
                f"✅ <b>Raw source saved</b>\n<code>{path.name}</code>\n\n"
                f"Run /secondbrain ingest to generate wiki page from it.",
                parse_mode="HTML",
            )

        # ── CREATE (manual wiki page) ─────────────────────────────────────────
        elif command == "create" and len(args) > 1:
            from skills.second_brain import create_wiki_page
            payload = " ".join(args[1:])
            if "|" not in payload:
                raise ValueError("Use: /secondbrain create title | content")
            title, body = [p.strip() for p in payload.split("|", 1)]
            path = create_wiki_page(title, body)
            await update.message.reply_text(
                f"✅ <b>Wiki page created</b>\n[[{path.stem}]]",
                parse_mode="HTML",
            )

        # ── DELETE ────────────────────────────────────────────────────────────
        elif command == "delete" and len(args) > 1:
            from skills.second_brain import delete_wiki_page
            page_name = args[1]
            ok = delete_wiki_page(page_name)
            if ok:
                await update.message.reply_text(f"🗑️ Deleted [[{page_name.replace('.md','')}]]")
            else:
                await update.message.reply_text(f"❌ Page not found: {page_name}")

        # ── INGEST ────────────────────────────────────────────────────────────
        elif command == "ingest":
            chat_id = update.effective_chat.id
            thinking = await update.message.reply_text(
                "⚡ <b>Ingesting raw sources...</b>\nAI is reading + generating wiki pages.",
                parse_mode="HTML",
            )
            import asyncio
            loop = asyncio.get_event_loop()
            def _run():
                from skills.second_brain import ingest_raw_sources
                return ingest_raw_sources()
            result = await loop.run_in_executor(None, _run)
            if result["status"] == "no_sources":
                await thinking.edit_text(
                    "⚠️ No raw sources to ingest.\nAdd sources first: /secondbrain newsource title | text",
                )
            else:
                pages_list = "\n".join(f"  • [[{p.replace('.md','')}]]" for p in result["pages"][:10])
                errors_text = ("\n⚠️ Errors:\n" + "\n".join(result["errors"][:3])) if result["errors"] else ""
                await thinking.edit_text(
                    f"✅ <b>Ingest Complete</b>\n\n"
                    f"Sources processed: {result['sources_processed']}\n"
                    f"Wiki pages created: {result['pages_created']}\n\n"
                    f"{pages_list}{errors_text}",
                    parse_mode="HTML",
                )

        # ── QUERY ─────────────────────────────────────────────────────────────
        elif command == "query" and len(args) > 1:
            save = args[1].lower() == "save"
            question = " ".join(args[2:] if save else args[1:])
            if not question:
                await update.message.reply_text("Usage: /secondbrain query [save] your question")
                return
            thinking = await update.message.reply_text("🧠 <i>Searching vault...</i>", parse_mode="HTML")
            import asyncio
            loop = asyncio.get_event_loop()
            def _run():
                from skills.second_brain import query_second_brain
                return query_second_brain(question, save_answer=save)
            answer = await loop.run_in_executor(None, _run)
            saved_note = "\n\n💾 <i>Saved as new wiki page.</i>" if save else ""
            await thinking.edit_text(
                f"❓ <b>{question[:80]}</b>\n\n{answer}{saved_note}",
                parse_mode="HTML",
            )

        # ── HEALTH ────────────────────────────────────────────────────────────
        elif command == "health":
            thinking = await update.message.reply_text(
                "🏥 <b>Running wiki health check...</b>\nAI auditing for contradictions + orphans.",
                parse_mode="HTML",
            )
            import asyncio
            loop = asyncio.get_event_loop()
            def _run():
                from skills.second_brain import health_check_wiki
                return health_check_wiki()
            report = await loop.run_in_executor(None, _run)
            await thinking.edit_text(report, parse_mode="HTML")

        # ── SYNC (bootstrap whole OpenClaw system into brain) ─────────────────
        elif command == "sync":
            thinking = await update.message.reply_text(
                "🔄 <b>Syncing OpenClaw system into second brain...</b>\n\n"
                "Reading: project wiki, agent files, architecture, command map...",
                parse_mode="HTML",
            )
            import asyncio
            loop = asyncio.get_event_loop()
            def _run():
                from skills.second_brain import sync_openclaw
                return sync_openclaw()
            result = await loop.run_in_executor(None, _run)
            pages_list = "\n".join(f"  • [[{p.replace('.md','')}]]" for p in result["pages"][:12])
            errors_text = ("\n⚠️ Errors: " + str(len(result["errors"]))) if result["errors"] else ""
            await thinking.edit_text(
                f"✅ <b>OpenClaw brain sync complete</b>\n\n"
                f"Sources ingested: {result['synced']}\n"
                f"Wiki pages generated: {result['wiki_pages']}\n\n"
                f"{pages_list}{errors_text}\n\n"
                f"Try: /secondbrain query what agents does openclaw have",
                parse_mode="HTML",
            )

        # ── LOG ───────────────────────────────────────────────────────────────
        elif command == "log" and len(args) > 1:
            payload = " ".join(args[1:])
            if "|" not in payload:
                raise ValueError("Use: /secondbrain log action | note")
            action, note = [p.strip() for p in payload.split("|", 1)]
            log_path = append_second_brain_log(action, note)
            await update.message.reply_text(
                f"✅ <b>Logged</b>\n<code>{action}</code> → {note[:80]}",
                parse_mode="HTML",
            )

        else:
            await update.message.reply_text(_HELP, parse_mode="HTML")

    except FileNotFoundError as exc:
        await update.message.reply_text(f"🚨 File error: {exc}")
    except Exception as exc:
        await update.message.reply_text(f"🚨 Second brain error: {exc}")


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
        "  /autotrade [on|off]  — fully auto daily trading\n"
        "  /trades [n]          — last N trade decisions\n"
        "  /report              — executed-trade activity summary\n\n"
        "<b>💻 PC Execution:</b>\n"
        "  /run [command]      — run shell command\n"
        "  /py [code]          — run Python code\n\n"
        "<b>⏰ Reminders:</b>\n"
        "  /remind HH:MM text  — set one-time reminder\n"
        "  /tasks              — list reminders\n"
        "  /cancel [id]        — cancel reminder\n\n"
        "<b>⚙️ System:</b>\n"
        "  /mode               — check trading mode\n"
        "  /live               — switch to LIVE trading\n"
        "  /demo               — switch to DEMO mode\n"
        "<b>🧠 Self-Improving:</b>\n"
        "  /selfimprove init               — create ~/self-improving/ memory files\n"
        "  /selfimprove status             — show memory and correction counts\n"
        "  /selfimprove show memory|corrections|heartbeat|index\n"
        "  /selfimprove log correction ... — log a correction entry\n"
        "  /selfimprove log memory ...     — log a reusable lesson\n"
        "  /secondbrain init               — create a second-brain Obsidian vault\n"
        "  /secondbrain status             — show second-brain vault status\n"
        "  /secondbrain show index|log|CLAUDE.md\n"
        "  /secondbrain newsource title | text — add a raw source file\n\n"
        "<b>🤖 Multi-Agent Orchestration:</b>\n"
        "  /orchestrate       — manage orchestrated tasks\n"
        "  /sweep             — mark stale orchestration tasks\n\n"
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
        try:
            cfg = sched.enable_autotrade(chat_id, scan_time=scan_time, timeframe=timeframe)
        except ValueError as exc:
            await update.message.reply_text(f"❌ {html.escape(str(exc))}")
            return
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
        await update.message.reply_text("<i>Running auto-trade scan now...</i>", parse_mode="HTML")
        await sched.run_autotrade_now()

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


# ── /hermes ───────────────────────────────────────────────────────────────────

async def cmd_hermes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    chat_id = update.effective_chat.id
    arg     = context.args[0].lower() if context.args else ""

    if arg == "on":
        scan_time = context.args[1] if len(context.args) > 1 else "09:30"
        try:
            cfg = sched.enable_hermes(chat_id, scan_time=scan_time)
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}", parse_mode="HTML")
            return
        await update.message.reply_text(
            f"🧠 <b>Hermes ENABLED</b>\n\n"
            f"⏰ Daily run: <code>{cfg['scan_time']} UTC</code>\n"
            f"📊 Builds graphify knowledge graph of the codebase\n"
            f"📝 Syncs digest to <code>memory/HERMES_GRAPH_REPORT.md</code>\n"
            f"🗂 Obsidian: <code>graphify-out/obsidian/</code>\n\n"
            f"<i>Use /hermes off to disable. /hermes now to run immediately.</i>",
            parse_mode="HTML",
        )

    elif arg == "off":
        sched.disable_hermes()
        await update.message.reply_text(
            "🧠 <b>Hermes DISABLED</b>\n\nNo more daily knowledge-graph builds. Use /hermes on to re-enable.",
            parse_mode="HTML",
        )

    elif arg == "now":
        await update.message.reply_text("<i>🧠 Running Hermes now…</i>", parse_mode="HTML")
        await sched.run_hermes_now(send_fn=_scheduler_send, chat_id=chat_id)

    else:
        cfg    = sched.get_hermes_status()
        status = "ENABLED ✅" if cfg.get("enabled") else "DISABLED ❌"
        await update.message.reply_text(
            f"🧠 <b>Hermes Status: {status}</b>\n\n"
            f"⏰ Daily run: <code>{cfg.get('scan_time', '09:30')} UTC</code>\n\n"
            f"<b>Commands:</b>\n"
            f"  /hermes on        — enable (09:30 UTC default)\n"
            f"  /hermes on HH:MM  — custom daily time\n"
            f"  /hermes off       — disable\n"
            f"  /hermes now       — run knowledge-graph build immediately",
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


# ── /trades ───────────────────────────────────────────────────────────────────

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    try:
        count = int(context.args[0]) if context.args else 10
        count = max(1, min(count, 30))
    except ValueError:
        count = 10

    from pathlib import Path as _Path
    log_file = _Path(__file__).parent.parent / "data" / "logs" / "trades.log"

    if not log_file.exists():
        await update.message.reply_text(
            "📋 <b>No trades logged yet.</b>\n\n"
            "<i>Trades appear here once the bot executes or simulates a signal.</i>",
            parse_mode="HTML",
        )
        return

    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        trade_lines = [l for l in lines if l.strip()]
        recent = trade_lines[-count:]
    except Exception as exc:
        await update.message.reply_text(f"🚨 Could not read trades.log: <code>{exc}</code>", parse_mode="HTML")
        return

    if not recent:
        await update.message.reply_text("📋 <b>No trades logged yet.</b>", parse_mode="HTML")
        return

    import json as _json
    parts = [f"📋 <b>Last {len(recent)} Trade(s):</b>\n"]
    for raw in recent:
        try:
            # Format: "TRADE_DECISION | timestamp | {json}"
            _, ts, payload = raw.split(" | ", 2)
            data = _json.loads(payload)
            action   = data.get("action", "?")
            coin     = data.get("coin", "?")
            status   = data.get("status", "?")
            ts_short = ts[:16]  # "2025-05-24T08:00"

            if status == "demo":
                emoji = "🟡"
                detail = f"${data.get('usd_amount', 0):.2f} @ ${data.get('price', 0):,.2f} [DEMO]"
            elif status == "executed":
                emoji = "🟢" if action == "BUY" else "🔴"
                detail = f"${data.get('usd_amount', 0):.2f} @ ${data.get('price', 0):,.2f}"
            elif status == "skipped":
                emoji = "⚪"
                detail = data.get("reason", "")[:60]
            else:
                emoji = "🚨"
                detail = data.get("reason", status)[:60]

            parts.append(f"{emoji} <code>{ts_short}</code> {action} {coin}\n   {detail}")
        except Exception:
            parts.append(f"<code>{raw[:120]}</code>")

    await update.message.reply_text("\n".join(parts), parse_mode="HTML")


# ── /mode /live /demo ─────────────────────────────────────────────────────────

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    from trading.mode import get_mode
    mode = get_mode()
    emoji = "⚡" if mode == "LIVE" else "🛡"
    await update.message.reply_text(
        f"Current mode: <b>{mode}</b> {emoji}\n\n"
        f"  /live — Switch to LIVE trading ⚡\n"
        f"  /demo — Switch to DEMO mode 🛡",
        parse_mode="HTML",
    )


async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    from trading.mode import set_mode, get_mode
    if get_mode() == "LIVE":
        await update.message.reply_text("⚡ Already in <b>LIVE</b> mode.", parse_mode="HTML")
        return
    set_mode("LIVE")
    await update.message.reply_text(
        "⚡ <b>Switched to LIVE trading mode.</b>\n\n"
        "⚠️ Real orders will now be placed on Crypto.com.\n"
        "Use /demo to switch back to safe DEMO mode.",
        parse_mode="HTML",
    )


async def cmd_demo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    from trading.mode import set_mode, get_mode
    if get_mode() == "DEMO":
        await update.message.reply_text("🛡 Already in <b>DEMO</b> mode.", parse_mode="HTML")
        return
    set_mode("DEMO")
    await update.message.reply_text(
        "🛡 <b>Switched to DEMO mode.</b>\n\n"
        "Signals will be evaluated and logged but no real orders will be placed.\n"
        "Use /live to enable real trading.",
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


# ── /pnl (simple trade-history P&L summary; distinct from the AI /report) ─────

async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    from trading.history import format_report, load_trades, summarize

    await update.message.reply_text(
        format_report(summarize(load_trades())), parse_mode="HTML"
    )


# ── Global error handler ──────────────────────────────────────────────────────

async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch any uncaught handler exception, log it, and tell the user politely."""
    logger.exception("Unhandled handler exception: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_chat is not None:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Internal error — check logs.",
            )
    except Exception:
        # Best-effort notification; never re-raise from the error handler.
        pass


# ── CashClaw Income Pipeline ──────────────────────────────────────────────────

async def cmd_fng(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fear & Greed Index from alternative.me."""
    if not is_authorized(update.effective_chat.id):
        return
    try:
        import requests as _req
        r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        d = r.json()["data"][0]
        val = int(d["value"])
        label = d["value_classification"]
        bar = "█" * (val // 10) + "░" * (10 - val // 10)
        emoji = "😱" if val < 25 else "😨" if val < 45 else "😐" if val < 55 else "😄" if val < 75 else "🤑"
        await update.message.reply_text(
            f"{emoji} <b>Fear & Greed Index</b>\n"
            f"<code>{bar}</code>\n"
            f"<b>{val}/100</b> — {label}\n"
            f"<i>via alternative.me</i>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ FNG fetch failed: {e}")


async def cmd_cashclaw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """CashClaw dashboard summary — pipeline status."""
    if not is_authorized(update.effective_chat.id):
        return
    try:
        from agents.job_scout import get_scout_status, format_scout_status
        from agents.cashclaw_applier import get_applier_status, format_applier_status
        scout   = get_scout_status()
        applier = get_applier_status()
        msg = (
            "🦞 <b>CashClaw Status</b>\n\n"
            f"🔍 <b>Scout</b>\n{format_scout_status(scout)}\n\n"
            f"📝 <b>Applier</b>\n{format_applier_status(applier)}\n\n"
            "<i>Commands: /scout run | /approve_job N | /apply_job N | /send_apply N</i>"
        )
        await update.message.reply_text(msg[:4000], parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ CashClaw error: {e}")


async def cmd_scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scout — show status. /scout run — trigger full scan."""
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args or []
    if args and args[0].lower() == "run":
        await update.message.reply_text("🔍 Running job scout… (this may take 30–60s)")
        try:
            from agents.job_scout import run_job_scout
            result = run_job_scout(bot=context.bot, chat_id=update.effective_chat.id)
            await update.message.reply_text(str(result)[:4000], parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Scout error: {e}")
    else:
        try:
            from agents.job_scout import get_scout_status, format_scout_status
            status = get_scout_status()
            await update.message.reply_text(
                f"🔍 <b>Job Scout</b>\n{format_scout_status(status)}", parse_mode="HTML"
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Scout error: {e}")


async def cmd_approve_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve_job N — approve job N from pending list."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve_job <number>")
        return
    try:
        idx = int(context.args[0]) - 1
        from agents.job_scout import approve_job
        result = approve_job(idx)
        if result.get("ok"):
            job = result.get("job", {})
            await update.message.reply_text(
                f"✅ <b>Approved:</b> {job.get('title','?')}\n"
                f"Platform: {job.get('platform','?')}\n"
                f"Use /apply_job {idx+1} to draft outreach.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(f"⚠️ {result.get('error','Unknown error')}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_apply_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/apply_job N [cold|followup] — generate humanized outreach for job N."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /apply_job <number> [cold|followup]")
        return
    try:
        idx   = int(context.args[0]) - 1
        style = context.args[1] if len(context.args) > 1 else "cold"
        await update.message.reply_text("✍️ Generating humanized outreach via HumanVoice…")
        from agents.cashclaw_applier import generate_apply, format_apply_preview
        result = generate_apply(idx, style=style)
        if result.get("ok"):
            preview = format_apply_preview(result)
            await update.message.reply_text(
                f"{preview}\n\n<i>/send_apply {result.get('draft_index',idx)+1} to confirm | "
                f"/discard_apply {result.get('draft_index',idx)+1} to drop</i>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(f"⚠️ {result.get('error','Failed')}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_send_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/send_apply N — confirm and mark draft N as sent."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /send_apply <draft_number>")
        return
    try:
        idx = int(context.args[0]) - 1
        from agents.cashclaw_applier import confirm_apply
        result = confirm_apply(idx)
        if result.get("ok"):
            await update.message.reply_text(
                f"📤 <b>Marked as sent!</b>\n"
                f"Job: {result.get('job_title','?')}\n"
                f"Total applied this cycle: {result.get('total_applied',0)}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(f"⚠️ {result.get('error','Failed')}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_discard_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/discard_apply N — discard draft N."""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /discard_apply <draft_number>")
        return
    try:
        idx = int(context.args[0]) - 1
        from agents.cashclaw_applier import discard_draft
        result = discard_draft(idx)
        if result.get("ok"):
            await update.message.reply_text(f"🗑 Draft {idx+1} discarded.")
        else:
            await update.message.reply_text(f"⚠️ {result.get('error','Failed')}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_log_income(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log_income <amount> <source> [note] — log real income earned."""
    if not is_authorized(update.effective_chat.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /log_income <amount> <source> [note]\n"
            "Example: /log_income 150 whop 'discord bot job'"
        )
        return
    try:
        from datetime import datetime, timezone as _tz
        import json as _json
        amount = float(context.args[0])
        source = context.args[1]
        note   = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        from pathlib import Path as _P
        data_dir = _P(__file__).parent.parent / "data"
        log_file = data_dir / "income_log.json"
        log = _json.loads(log_file.read_text()) if log_file.exists() else []
        entry = {
            "amount":    amount,
            "source":    source,
            "note":      note,
            "timestamp": datetime.now(_tz.utc).isoformat(),
        }
        log.append(entry)
        log_file.write_text(_json.dumps(log, indent=2))
        running = sum(float(e.get("amount", 0)) for e in log)
        await update.message.reply_text(
            f"💰 <b>Income logged!</b>\n"
            f"Amount: <b>${amount:.2f}</b>\n"
            f"Source: {source}\n"
            f"Note: {note or '—'}\n"
            f"Running total: <b>${running:.2f}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_sweep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sweep — mark stale orchestration tasks (>48h no update) as stale."""
    if not is_authorized(update.effective_chat.id):
        return
    try:
        from skills.agent_team_orchestrator import sweep_stale_tasks
        expired = sweep_stale_tasks(ttl_hours=48)
        if expired:
            await update.message.reply_text(
                f"🧹 Swept {len(expired)} stale task(s):\n" + "\n".join(f"• {t[-8:]}" for t in expired)
            )
        else:
            await update.message.reply_text("✅ No stale tasks found.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Sweep error: {e}")


# ── Clip Pipeline ─────────────────────────────────────────────────────────────

async def cmd_clip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download + process a VOD into viral clips. Usage: /clip <url> [duration_sec]"""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/clip &lt;url&gt; [duration_sec]</code>\n"
            "Example: /clip https://youtube.com/watch?v=... 60",
            parse_mode="HTML",
        )
        return
    url = context.args[0]
    duration = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 60
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🎬 <b>Clip job queued</b>\nURL: <code>{url[:60]}</code>\n"
        f"Clip length: {duration}s\n\nProcessing in background — I'll update you.",
        parse_mode="HTML",
    )
    import asyncio
    loop = asyncio.get_event_loop()
    def _run():
        try:
            from agents.clip_processor import process_vod_url
            process_vod_url(url, clip_duration=duration, bot=_app.bot, chat_id=chat_id)
        except Exception as exc:
            import logging
            logging.getLogger("openclaw.clip").error("clip job error: %s", exc)
    loop.run_in_executor(None, _run)


async def cmd_clips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all clip jobs. Usage: /clips"""
    if not is_authorized(update.effective_chat.id):
        return
    try:
        from agents.clip_processor import get_clip_jobs, format_clip_jobs_summary
        jobs = get_clip_jobs()
        await update.message.reply_text(format_clip_jobs_summary(jobs), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Clips error: {e}")


# ── Content Pipeline ──────────────────────────────────────────────────────────

async def cmd_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Push a clip through content pipeline (9:16 + captions). Usage: /content <clip_path>"""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        try:
            from agents.content_pipeline import get_content_queue
            q = get_content_queue()
            msg = (
                f"📋 <b>Content Queue</b>\n\n"
                f"Queued: {q.get('queued', 0)} | Approved: {q.get('approved', 0)} | "
                f"Posted: {q.get('total_posted', 0)} | Failed: {q.get('total_failed', 0)}\n\n"
                "To process a clip:\n<code>/content &lt;clip_path&gt; [context]</code>"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Content queue error: {e}")
        return

    clip_path = context.args[0]
    context_text = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🎬 <b>Content pipeline running</b>\nClip: <code>{clip_path}</code>",
        parse_mode="HTML",
    )
    import asyncio
    loop = asyncio.get_event_loop()
    def _run():
        try:
            from agents.content_pipeline import run_content_pipeline
            run_content_pipeline(clip_path, context=context_text, bot=_app.bot, chat_id=chat_id)
        except Exception as exc:
            import logging
            logging.getLogger("openclaw.content").error("content pipeline error: %s", exc)
    loop.run_in_executor(None, _run)


async def cmd_approve_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a queued content item. Usage: /approve_content <id> [1|2|3]"""
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: <code>/approve_content &lt;id&gt; [1|2|3]</code>", parse_mode="HTML")
        return
    item_id = context.args[0]
    cap_idx = int(context.args[1]) - 1 if len(context.args) > 1 and context.args[1].isdigit() else 0
    try:
        from agents.content_pipeline import approve_content
        result = approve_content(item_id, cap_idx)
        await update.message.reply_text(result, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Approve error: {e}")


# ── Social Publisher ──────────────────────────────────────────────────────────

async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger social publisher (preview queued items / start posting). Usage: /publish [now]"""
    if not is_authorized(update.effective_chat.id):
        return
    force_now = bool(context.args and context.args[0].lower() == "now")
    chat_id = update.effective_chat.id
    await update.message.reply_text("📤 <b>Publisher running...</b>", parse_mode="HTML")
    import asyncio
    loop = asyncio.get_event_loop()
    def _run():
        try:
            from agents.social_publisher import run_social_publisher, send_preview
            if force_now:
                run_social_publisher(bot=_app.bot, chat_id=chat_id)
            else:
                send_preview(bot=_app.bot, chat_id=chat_id)
        except Exception as exc:
            import logging
            logging.getLogger("openclaw.publisher").error("publisher error: %s", exc)
    loop.run_in_executor(None, _run)


async def cmd_publishstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show social publishing stats. Usage: /publishstats"""
    if not is_authorized(update.effective_chat.id):
        return
    try:
        from agents.social_publisher import get_publish_stats
        stats = get_publish_stats()
        msg = (
            f"📊 <b>Publish Stats</b>\n\n"
            f"Total posted: {stats.get('total_posted', 0)}\n"
            f"TikTok: {stats.get('tiktok_posted', 0)} | IG: {stats.get('instagram_posted', 0)}\n"
            f"Failed: {stats.get('total_failed', 0)}\n"
            f"Last run: {stats.get('last_run', 'never')}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Publish stats error: {e}")


# ── Trading Agent ─────────────────────────────────────────────────────────────

async def cmd_tradingagent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Autonomous trading agent. Usage: /tradingagent [status|cycle|optimize <coin>|dca <coin> <amount> <hours>]"""
    if not is_authorized(update.effective_chat.id):
        return
    sub = context.args[0].lower() if context.args else "status"
    chat_id = update.effective_chat.id

    if sub == "status":
        try:
            from agents.trading_agent import get_trading_status
            st = get_trading_status()
            msg = (
                f"🤖 <b>Trading Agent Status</b>\n\n"
                f"Last cycle: {st.get('last_cycle', 'never')}\n"
                f"DCA schedules: {len(st.get('dca_schedules', []))}\n"
                f"Last optimizer: {st.get('last_optimizer_run', 'never')}\n"
                f"Active signals: {st.get('active_signals', 0)}"
            )
            await update.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Trading status error: {e}")

    elif sub == "cycle":
        await update.message.reply_text("⚡ <b>Running trading cycle...</b>", parse_mode="HTML")
        import asyncio
        loop = asyncio.get_event_loop()
        def _run():
            try:
                from agents.trading_agent import run_trading_cycle
                run_trading_cycle(bot=_app.bot, chat_id=chat_id)
            except Exception as exc:
                import logging
                logging.getLogger("openclaw.trading_agent").error("cycle error: %s", exc)
        loop.run_in_executor(None, _run)

    elif sub == "optimize" and len(context.args) > 1:
        coin = context.args[1].upper()
        await update.message.reply_text(f"🔬 <b>Optimizing strategy for {coin}...</b>", parse_mode="HTML")
        import asyncio
        loop = asyncio.get_event_loop()
        def _run():
            try:
                from agents.trading_agent import trigger_optimize
                result = trigger_optimize(coin)
                asyncio.run_coroutine_threadsafe(
                    update.message.reply_text(
                        f"✅ Optimize complete for {coin}\nBest Sharpe: {result.get('best_sharpe', '?')}",
                        parse_mode="HTML",
                    ),
                    loop,
                )
            except Exception as exc:
                import logging
                logging.getLogger("openclaw.trading_agent").error("optimize error: %s", exc)
        loop.run_in_executor(None, _run)

    elif sub == "dca" and len(context.args) >= 4:
        coin, amount, hours = context.args[1].upper(), context.args[2], context.args[3]
        try:
            from agents.trading_agent import set_dca
            result = set_dca(coin, float(amount), int(hours))
            await update.message.reply_text(f"✅ {result}", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ DCA setup error: {e}")

    else:
        await update.message.reply_text(
            "<b>Trading Agent</b>\n\n"
            "/tradingagent status\n"
            "/tradingagent cycle\n"
            "/tradingagent optimize BTC_USDT\n"
            "/tradingagent dca BTC_USDT 50 24",
            parse_mode="HTML",
        )


# ── Performance Tracker ───────────────────────────────────────────────────────

async def cmd_performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Content performance stats (TikTok/IG views + income). Usage: /performance [snapshot]"""
    if not is_authorized(update.effective_chat.id):
        return
    sub = context.args[0].lower() if context.args else "summary"
    if sub == "snapshot":
        await update.message.reply_text("📸 <b>Taking performance snapshot...</b>", parse_mode="HTML")
        import asyncio
        loop = asyncio.get_event_loop()
        def _run():
            try:
                from agents.performance_tracker import run_performance_tracker
                run_performance_tracker(bot=_app.bot, chat_id=update.effective_chat.id)
            except Exception as exc:
                import logging
                logging.getLogger("openclaw.perf").error("tracker error: %s", exc)
        loop.run_in_executor(None, _run)
        return
    try:
        from agents.performance_tracker import get_performance_summary
        s = get_performance_summary()
        proj = s.get("income_projections", {})
        msg = (
            f"📈 <b>Performance Summary</b>\n\n"
            f"TikTok posts tracked: {s.get('tiktok_posts', 0)}\n"
            f"IG posts tracked: {s.get('instagram_posts', 0)}\n"
            f"Total views: {s.get('total_views', 0):,}\n\n"
            f"<b>Income Projections (monthly):</b>\n"
            f"Conservative: ${proj.get('conservative', 0):.2f}\n"
            f"Current: ${proj.get('current', 0):.2f}\n"
            f"Optimized: ${proj.get('optimized', 0):.2f}\n\n"
            "Run /performance snapshot to refresh data."
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Performance error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    if not (os.getenv("ALLOWED_CHAT_ID", "").strip() or os.getenv("ALLOWED_CHAT_IDS", "").strip()):
        print("⚠️  WARNING: ALLOWED_CHAT_ID / ALLOWED_CHAT_IDS is not set in .env")
        print("   The bot will start but will silently ignore ALL messages.")
        print("   Get your chat ID from @userinfobot, then add it to .env")

    sched.set_send_fn(_scheduler_send)
    sched.start_scheduler()
    sched.reload_autotrade(from_env=bool(os.getenv("AUTOTRADE_ENABLED", "").strip()))   # re-register daily job if it was enabled before restart
    sched.reload_hermes()

    _app = Application.builder().token(token).build()

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
    _app.add_handler(CommandHandler("trades",   cmd_trades))
    _app.add_handler(CommandHandler("weather",  cmd_weather))
    _app.add_handler(CommandHandler("hermes",    cmd_hermes))
    _app.add_handler(CommandHandler("mode",      cmd_mode))
    _app.add_handler(CommandHandler("live",      cmd_live))
    _app.add_handler(CommandHandler("demo",      cmd_demo))
    _app.add_handler(CommandHandler("pnl",       cmd_pnl))
    _app.add_handler(CommandHandler("stop",      cmd_stop))
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
    _app.add_handler(CommandHandler("secondbrain", cmd_secondbrain))
    _app.add_handler(CommandHandler("upgrade",    cmd_upgrade))
    _app.add_handler(CommandHandler("restart",    cmd_restart))
    # CashClaw income pipeline
    _app.add_handler(CommandHandler("fng",          cmd_fng))
    _app.add_handler(CommandHandler("cashclaw",     cmd_cashclaw))
    _app.add_handler(CommandHandler("scout",        cmd_scout))
    _app.add_handler(CommandHandler("approve_job",  cmd_approve_job))
    _app.add_handler(CommandHandler("apply_job",    cmd_apply_job))
    _app.add_handler(CommandHandler("send_apply",   cmd_send_apply))
    _app.add_handler(CommandHandler("discard_apply", cmd_discard_apply))
    _app.add_handler(CommandHandler("log_income",   cmd_log_income))
    _app.add_handler(CommandHandler("sweep",        cmd_sweep))
    # Clip + Content pipeline
    _app.add_handler(CommandHandler("clip",            cmd_clip))
    _app.add_handler(CommandHandler("clips",           cmd_clips))
    _app.add_handler(CommandHandler("content",         cmd_content))
    _app.add_handler(CommandHandler("approve_content", cmd_approve_content))
    _app.add_handler(CommandHandler("publish",         cmd_publish))
    _app.add_handler(CommandHandler("publishstats",    cmd_publishstats))
    _app.add_handler(CommandHandler("tradingagent",    cmd_tradingagent))
    _app.add_handler(CommandHandler("performance",     cmd_performance))
    # LifeOS
    _app.add_handler(CommandHandler("lifeos",       cmd_lifeos))
    _app.add_handler(CommandHandler("morning",      cmd_morning))
    _app.add_handler(CommandHandler("evening",      cmd_evening))
    _app.add_handler(CommandHandler("score",        cmd_score))
    _app.add_handler(CommandHandler("logweight",    cmd_logweight))
    _app.add_handler(CommandHandler("logexpense",   cmd_logexpense))
    _app.add_handler(CommandHandler("logincome",    cmd_logincome))
    _app.add_handler(CommandHandler("lifesetup",    cmd_lifesetup))
    _app.add_handler(CommandHandler("lifemode",     cmd_lifemode))
    _app.add_handler(CommandHandler("lifeschedule", cmd_lifeschedule))
    _app.add_handler(CommandHandler("dash",         cmd_dash))

    # Free-text conversation (must be last — catches all non-command text)
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Global error handler so uncaught exceptions don't go silent.
    _app.add_error_handler(_on_error)

    # ── Set Telegram command menu (visible when user taps the ☰ menu button) ──
    from telegram import BotCommand
    _commands = [
        BotCommand("start",       "Show all commands"),
        BotCommand("help",        "Show all commands"),
        BotCommand("ask",         "Ask ClawBot anything"),
        BotCommand("market",      "Live crypto prices + AI analysis"),
        BotCommand("scan",        "RSI+MACD signal scan [1h|4h|1d]"),
        BotCommand("dca",         "DCA entry analysis [coin]"),
        BotCommand("autotrade",   "Auto-trading [on|off|status|now]"),
        BotCommand("hermes",      "Hermes knowledge-graph job [on|off|now]"),
        BotCommand("mode",        "Check trading mode"),
        BotCommand("live",        "Switch to LIVE trading"),
        BotCommand("demo",        "Switch to DEMO mode"),
        BotCommand("weather",     "Current weather [city]"),
        BotCommand("news",        "Check macro news / trading block"),
        BotCommand("report",      "Trade performance report"),
        BotCommand("pnl",         "Simple trade-history P&L summary"),
        BotCommand("backtest",    "Backtest results [run]"),
        BotCommand("remind",      "Set reminder HH:MM text"),
        BotCommand("tasks",       "List pending reminders"),
        BotCommand("save",        "Save a note to knowledge base"),
        BotCommand("notes",       "View / search saved notes"),
        BotCommand("selfimprove", "Self-improving memory + corrections"),
        BotCommand("secondbrain", "Manage second-brain vault"),
        BotCommand("orchestrate", "Manage orchestrated tasks"),
        BotCommand("otasks",      "List orchestration tasks"),
        BotCommand("run",         "Run shell command on this PC"),
        BotCommand("py",          "Run Python code on this PC"),
        BotCommand("status",      "System status check"),
        BotCommand("brain",       "AI usage stats today"),
        BotCommand("codereview",  "AI code self-review"),
        BotCommand("upgrade",     "Auto-fix bot [dry run | apply | review]"),
        BotCommand("trades",      "Recent trade history"),
        BotCommand("restart",     "Restart ClawBot"),
        BotCommand("stop",        "Graceful shutdown"),
        BotCommand("fng",         "Fear & Greed Index"),
        BotCommand("cashclaw",    "CashClaw income pipeline status"),
        BotCommand("scout",       "Job scout [run]"),
        BotCommand("approve_job", "Approve scouted job <N>"),
        BotCommand("apply_job",   "Generate application draft <N>"),
        BotCommand("send_apply",  "Send approved draft <N>"),
        BotCommand("discard_apply", "Discard draft <N>"),
        BotCommand("log_income",  "Log income: amount source [note]"),
        BotCommand("sweep",       "Sweep stale orchestrator tasks"),
        BotCommand("clip",            "Download + clip a VOD [url] [sec]"),
        BotCommand("clips",           "List all clip jobs"),
        BotCommand("content",         "Content pipeline: 9:16 + captions [clip_path]"),
        BotCommand("approve_content", "Approve queued content <id> [1|2|3]"),
        BotCommand("publish",         "Publish queued content [now]"),
        BotCommand("publishstats",    "Social publishing stats"),
        BotCommand("tradingagent",    "Autonomous trading [status|cycle|optimize|dca]"),
        BotCommand("performance",     "Content performance + income projections [snapshot]"),
        BotCommand("lifeos",      "LifeOS dashboard summary"),
        BotCommand("morning",     "Morning check-in"),
        BotCommand("evening",     "Evening check-in"),
        BotCommand("score",       "Points + streak"),
        BotCommand("dash",        "Full mobile dashboard"),
    ]

    async def _post_init(application):
        # Scheduler + autotrade/hermes jobs are already started/reloaded in
        # main() above (before the event loop starts running); nothing to
        # redo here. Just register the interval/cron jobs that need a live
        # `application.bot` instance.

        # Register background agent jobs
        _owner_id = int(os.getenv("ALLOWED_CHAT_ID", "0").split(",")[0].strip() or "0")
        _raw_sched = sched.get_scheduler()
        if _raw_sched and _owner_id:
            try:
                from agents.news_filter_agent import check_and_alert as _news_alert
                _raw_sched.add_job(
                    _news_alert, "interval", minutes=15,
                    id="news_filter", replace_existing=True,
                    kwargs={"bot": application.bot, "chat_id": _owner_id},
                )
                print("✅ News filter job registered (every 15 min)")
            except Exception as _e:
                print(f"⚠️  News alert job not started: {_e}")
            try:
                from agents.code_review_agent import schedule_weekly_review
                schedule_weekly_review(_raw_sched, application.bot, _owner_id)
                print("✅ Weekly code review job registered (Sunday 09:00 UTC)")
            except Exception as _e:
                print(f"⚠️  Code review job not started: {_e}")
            try:
                from agents.job_scout import run_job_scout as _run_scout
                _raw_sched.add_job(
                    _run_scout, "interval", hours=6,
                    id="cashclaw_scout", replace_existing=True,
                )
                print("✅ CashClaw scout job registered (every 6h)")
            except Exception as _e:
                print(f"⚠️  CashClaw scout job not started: {_e}")
            try:
                from skills.agent_team_orchestrator import sweep_stale_tasks as _sweep
                _raw_sched.add_job(
                    _sweep, "interval", hours=12,
                    id="stale_sweep", replace_existing=True,
                )
                print("✅ Stale task sweep job registered (every 12h)")
            except Exception as _e:
                print(f"⚠️  Stale sweep job not started: {_e}")
            # ── Autopilot daily revenue engines ──────────────────────────
            try:
                from agents.trading_agent import run_trading_cycle as _trade_cycle
                _raw_sched.add_job(
                    _trade_cycle, "interval", hours=4,
                    id="trading_cycle", replace_existing=True,
                    kwargs={"bot": application.bot, "chat_id": _owner_id},
                )
                print("✅ Trading agent cycle registered (every 4h)")
            except Exception as _e:
                print(f"⚠️  Trading cycle job not started: {_e}")
            try:
                from agents.performance_tracker import run_performance_tracker as _perf
                _raw_sched.add_job(
                    _perf, "interval", hours=6,
                    id="perf_tracker", replace_existing=True,
                    kwargs={"bot": application.bot, "chat_id": _owner_id},
                )
                print("✅ Performance tracker registered (every 6h)")
            except Exception as _e:
                print(f"⚠️  Performance tracker not started: {_e}")
            try:
                from agents.social_publisher import send_preview as _preview
                _raw_sched.add_job(
                    _preview, "cron", hour=9, minute=0,
                    id="daily_publish_preview", replace_existing=True,
                    kwargs={"bot": application.bot, "chat_id": _owner_id},
                )
                print("✅ Daily publish preview registered (09:00 UTC)")
            except Exception as _e:
                print(f"⚠️  Publish preview not started: {_e}")
            try:
                from skills.second_brain import ingest_raw_sources as _sb_ingest
                _raw_sched.add_job(
                    _sb_ingest, "cron", day_of_week="sun", hour=10, minute=0,
                    id="secondbrain_ingest", replace_existing=True,
                )
                print("✅ Second brain weekly ingest registered (Sunday 10:00 UTC)")
            except Exception as _e:
                print(f"⚠️  Second brain ingest not started: {_e}")

        await application.bot.set_my_commands(_commands)
        # Send "back online" message to owner on every startup
        owner_id = int(os.getenv("ALLOWED_CHAT_ID", "0").split(",")[0].strip() or "0")
        if owner_id:
            try:
                from core import __version__ as _bot_version
                await application.bot.send_message(
                    owner_id,
                    f"🟢 <b>ClawBot v{_bot_version} is back online!</b>\n"
                    "<i>All systems go. Command menu updated.</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    _app.post_init = _post_init

    # Start JARVIS Gateway
    try:
        from core.jarvis_gateway import start_gateway_thread
        _gw_token = os.getenv("GATEWAY_TOKEN", "").strip()
        if _gw_token:
            start_gateway_thread()
            _gw_port = int(os.getenv("JARVIS_GATEWAY_PORT", "18790"))
            logger.info(f"JARVIS Gateway started on ws://127.0.0.1:{_gw_port}")
        else:
            logger.info("JARVIS Gateway: set GATEWAY_TOKEN in .env to enable")
    except Exception as _gw_err:
        logger.warning(f"JARVIS Gateway not started: {_gw_err}")

    from core import __version__ as _bot_version
    print(f"🦾 ClawBot v{_bot_version} running.")
    print("   Chat freely or use commands. /help for the full list.")

    # PTB installs OS signal handlers by default, which only works from the
    # main thread. start.py runs this bot in a background thread (Flask owns
    # the main thread there), so skip signal handling in that case — running
    # standalone via `python -m content.receiver` still gets normal ctrl-c
    # shutdown.
    run_kwargs = {"allowed_updates": Update.ALL_TYPES, "drop_pending_updates": True}
    if threading.current_thread() is not threading.main_thread():
        run_kwargs["stop_signals"] = None
    _app.run_polling(**run_kwargs)


if __name__ == "__main__":
    main()
