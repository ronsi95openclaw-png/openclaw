"""ClawBot v0.6 — Business AI Partner + Trading Bot

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

  PC Execution:
    /run [command]     — run a shell command on this PC
    /py [code]         — run Python code on this PC

  Reminders:
    /remind HH:MM text — set a daily reminder (UTC)
    /tasks             — list pending reminders
    /cancel <id>       — cancel a reminder

  System:
    /status            — bot + Ollama health check
    /brain             — AI usage stats
    /weather [city]    — current weather
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

from core.brain import CLAWBOT_SYSTEM, classify_complexity, get_usage_today
from core.conversation import add_message, clear_history, get_history
from core import scheduler as sched
from core.startup import ensure_data_dirs
from core.event_bus import register as bus_register, emit as bus_emit
from core.task_queue import run_in_pool
from lib.brain import brain
from lib.humanizer import humanize
from security.whitelist import is_authorized

ensure_data_dirs()

logger = logging.getLogger("openclaw.receiver")

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
        "<b>⚙️ System:</b>\n"
        "  /status  /brain  /weather [city]  /stop",
        parse_mode="HTML",
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
        result   = brain(text, purpose="chat", history=history)
        response = result["text"]
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"🦾 <b>ClawBot</b> <i>({result['brain']})</i>\n\n{humanize(response)}",
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
        result   = brain(prompt, purpose="chat", history=history)
        response = result["text"]
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"🦾 <b>ClawBot</b> <i>({result['brain']})</i>\n\n{response}",
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
        result   = brain(prompt, purpose="high_stakes", history=history)
        response = result["text"]
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
        result   = brain(prompt, purpose="analysis", history=history)
        response = result["text"]
        add_message(chat_id, "assistant", response)
        await thinking_msg.edit_text(
            f"🔬 <b>Research: {topic[:40]}</b>\n\n{response}", parse_mode="HTML"
        )
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
                    lines.append(f"⚪ {coin}: RSI <code>{rsi:.1f}</code> {trend}")
                except Exception:
                    lines.append(f"⚪ {coin}: insufficient data")
            lines.append("\n<i>All coins neutral. Watching.</i>")
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
        result   = brain(prompt, purpose="high_stakes")
        response = result["text"]
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
    reminders = sched.get_reminders(update.effective_chat.id)
    if not reminders:
        await update.message.reply_text("📋 No pending reminders.\n\n/remind HH:MM text to add one.")
        return

    lines = ["📋 <b>Pending Reminders:</b>\n"]
    for r in reminders:
        lines.append(
            f"⏰ <code>{r['time']} UTC</code> — {r['text']}\n"
            f"   <i>/cancel <code>{r['id']}</code></i>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
        "<b>⚙️ System:</b>\n"
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


# ── Video upload → content pipeline ──────────────────────────────────────────

# In-memory store: chat_id → (reel_path, captions) awaiting /approve
_pending_reels: dict[int, tuple] = {}


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a video file, run the full pipeline in the thread pool, prompt for approval."""
    if not is_authorized(update.effective_chat.id):
        return

    chat_id = update.effective_chat.id
    msg     = update.message

    file_obj = msg.video or msg.document
    if file_obj is None:
        return

    status_msg = await msg.reply_text(
        "🎬 <b>Video received</b> — downloading and processing...\n"
        "<i>Whisper + FFmpeg running in the background.</i>",
        parse_mode="HTML",
    )

    try:
        # Download to a temp path
        import tempfile
        tg_file  = await context.bot.get_file(file_obj.file_id)
        suffix   = Path(file_obj.file_name).suffix if hasattr(file_obj, "file_name") and file_obj.file_name else ".mp4"
        tmp      = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        await tg_file.download_to_drive(tmp_path)

        await status_msg.edit_text(
            "⚙️ <b>Stage 1/3</b> — Editing (trim, 9:16, captions, music)...",
            parse_mode="HTML",
        )

        # Heavy work in thread pool — does NOT block the event loop
        from content.pipeline import process
        result = await run_in_pool(process, tmp_path, return_artifacts=True)

        if result is None:
            await status_msg.edit_text("✅ Pipeline complete. Check Telegram for the reel.")
            return

        reel_path, captions = result
        _pending_reels[chat_id] = (reel_path, captions)

        await status_msg.edit_text(
            "✅ <b>Reel ready!</b>\n\n"
            f"<b>Instagram:</b>\n<code>{captions.instagram[:300]}</code>\n\n"
            f"<b>TikTok:</b>\n<code>{captions.tiktok[:150]}</code>\n\n"
            "Reply <b>/approve</b> to post to TikTok + Instagram\n"
            "or <b>/reject</b> to discard.",
            parse_mode="HTML",
        )

    except Exception as exc:
        await status_msg.edit_text(
            f"🚨 <b>Pipeline failed:</b>\n<code>{exc}</code>", parse_mode="HTML"
        )
        logger.error(f"Video pipeline error: {exc}")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Post the pending reel to TikTok + Instagram."""
    if not is_authorized(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    pending = _pending_reels.pop(chat_id, None)
    if pending is None:
        await update.message.reply_text("No reel pending approval. Upload a video first.")
        return

    reel_path, captions = pending
    thinking_msg = await update.message.reply_text(
        "📱 <b>Posting to TikTok + Instagram...</b>", parse_mode="HTML"
    )
    try:
        from content.poster import post_to_socials_sync
        result = await run_in_pool(post_to_socials_sync, reel_path, captions)
        await thinking_msg.edit_text(f"✅ <b>Posted!</b>\n\n{result}", parse_mode="HTML")
    except Exception as exc:
        await thinking_msg.edit_text(
            f"🚨 <b>Post failed:</b>\n<code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Discard the pending reel."""
    if not is_authorized(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    if _pending_reels.pop(chat_id, None) is not None:
        await update.message.reply_text("🗑 Reel discarded.")
    else:
        await update.message.reply_text("No reel pending.")


# ── /metrics ──────────────────────────────────────────────────────────────────

async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show tier usage distribution and recent misroutes from memory logs."""
    if not is_authorized(update.effective_chat.id):
        return

    memory_dir = Path(__file__).parent.parent / "memory"
    lines_out  = ["📊 <b>System Metrics</b>\n"]

    # Tier distribution from tier-usage.jsonl
    tier_counts: dict[str, int] = {}
    tier_file = memory_dir / "tier-usage.jsonl"
    if tier_file.exists():
        for raw in tier_file.read_text(encoding="utf-8").splitlines()[-200:]:
            try:
                entry = __import__("json").loads(raw)
                t = entry.get("tier", "?")
                tier_counts[t] = tier_counts.get(t, 0) + 1
            except Exception:
                pass

    if tier_counts:
        total = sum(tier_counts.values())
        lines_out.append("<b>Tier distribution (last 200 requests):</b>")
        for tier, count in sorted(tier_counts.items()):
            pct = count / total * 100
            lines_out.append(f"  {tier}: {count} ({pct:.0f}%)")
        lines_out.append("")

    # Recent soft-failures
    fail_file = memory_dir / "soft-failures.jsonl"
    if fail_file.exists():
        fails = fail_file.read_text(encoding="utf-8").splitlines()[-10:]
        if fails:
            lines_out.append("<b>Recent misroutes / inefficiencies:</b>")
            for raw in fails:
                try:
                    e = __import__("json").loads(raw)
                    lines_out.append(f"  {e.get('ts','')[:16]}  {e.get('reason','?')}  (tier={e.get('tier','?')})")
                except Exception:
                    pass
        else:
            lines_out.append("No soft-failures logged yet.")
    else:
        lines_out.append("No soft-failures logged yet.")

    # Tool cache stats
    from lib.tool_cache import cache_stats
    cs = cache_stats()
    lines_out.append(f"\n<b>Tool cache:</b> {cs['entries']} entries, {cs['size_kb']} KB")

    await update.message.reply_text("\n".join(lines_out), parse_mode="HTML")


# ── /stop ─────────────────────────────────────────────────────────────────────

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "👋 <b>ClawBot shutting down.</b> See you next time!", parse_mode="HTML"
    )
    os.kill(os.getpid(), signal.SIGINT)


# ── Entry point ───────────────────────────────────────────────────────────────

async def _bus_handle_message(payload: dict) -> str:
    """Event bus handler for 'user.message' — routes through brain()."""
    result = brain(
        prompt=payload.get("text", ""),
        purpose="chat",
        history=payload.get("history"),
    )
    return result["text"]


async def _bus_handle_market(payload: dict) -> str:
    """Event bus handler for 'market.update' — runs market summary."""
    from core.market import get_market_summary
    return get_market_summary()


async def _bus_handle_signal(payload: dict) -> dict:
    """Event bus handler for 'signal.generated' — LLM signal enrichment."""
    from trading.analyzer import enrich_signals
    signals = payload.get("signals", [])
    portfolio_usd = payload.get("portfolio_usd", 0.0)
    return enrich_signals(signals, portfolio_usd)


def main() -> None:
    global _app

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    # Wire event bus handlers
    bus_register("user.message",    _bus_handle_message)
    bus_register("market.update",   _bus_handle_market)
    bus_register("signal.generated", _bus_handle_signal)

    sched.set_send_fn(_scheduler_send)
    sched.start_scheduler()
    sched.reload_autotrade()   # re-register daily job if it was enabled before restart

    _app = Application.builder().token(token).build()

    # Commands
    _app.add_handler(CommandHandler("start",    cmd_start))
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
    _app.add_handler(CommandHandler("autotrade", cmd_autotrade))
    _app.add_handler(CommandHandler("approve",   cmd_approve))
    _app.add_handler(CommandHandler("reject",    cmd_reject))
    _app.add_handler(CommandHandler("metrics",   cmd_metrics))
    _app.add_handler(CommandHandler("stop",      cmd_stop))

    # Video uploads → content pipeline (non-blocking via task queue)
    _app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    # Free-text conversation (must be last — catches all non-command text)
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🦾 ClawBot v0.6 running.")
    print("   Chat freely or use commands. /help for the full list.")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
