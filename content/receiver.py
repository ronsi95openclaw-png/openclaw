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

from core.brain import CLAWBOT_SYSTEM, ask_hybrid, classify_complexity, get_usage_today
from core.conversation import add_message, clear_history, get_history
from core import scheduler as sched
from security.whitelist import is_authorized

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
        "<b>📊 Research &amp; Quant (Phase 11):</b>\n"
        "  /expectancy         — edge, profit factor, streaks\n"
        "  /sharpe             — Sharpe, Sortino, Calmar, Omega\n"
        "  /drawdown           — max DD, duration, recovery\n"
        "  /regime             — BTC/ETH/SOL market regime\n"
        "  /portfolioheat      — strategy weights + exposure\n"
        "  /montecarlo         — 1,000-path simulation\n"
        "  /executionquality   — slippage, fill quality\n"
        "  /liquidity          — live liquidity conditions\n"
        "  /slippage           — slippage analysis\n"
        "  /walkforward        — validation results\n"
        "  /optimizer          — best known parameters\n\n"
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


# ── Phase 11: Research + Quant Commands ──────────────────────────────────────
#
# All commands below are READ-ONLY analytics.
# They never place orders, modify state, or bypass risk controls.
# All imports are guarded — gracefully degrade if research modules not yet loaded.

def _truncate(text: str, max_len: int = 3800) -> str:
    """Truncate response to Telegram's 4096-char limit with safe margin."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n<i>... (truncated)</i>"


def _load_blofin_trades() -> list:
    """Load closed trades from blofin_state.json for analytics."""
    try:
        import json
        path = Path(__file__).parent.parent / "data" / "blofin_state.json"
        if not path.exists():
            return []
        state = json.loads(path.read_text())
        return state.get("trade_log", [])
    except Exception:
        return []


async def cmd_expectancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show trade expectancy, profit factor, payoff ratio, and streak analysis."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text("<i>Computing expectancy...</i>", parse_mode="HTML")

    try:
        trades = _load_blofin_trades()
        if not trades:
            await thinking.edit_text(
                "📊 <b>Expectancy</b>\n\nNo closed trades yet. Run the bot to generate trade history.",
                parse_mode="HTML",
            )
            return

        wins   = [t for t in trades if t.get("outcome") == "win"]
        losses = [t for t in trades if t.get("outcome") == "loss"]
        n      = len(trades)
        win_rate   = len(wins) / n if n > 0 else 0.0
        avg_win    = sum(t.get("pnl", 0) for t in wins)    / len(wins)    if wins   else 0.0
        avg_loss   = abs(sum(t.get("pnl", 0) for t in losses) / len(losses)) if losses else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        gross_win  = sum(t.get("pnl", 0) for t in wins   if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in losses if t.get("pnl", 0) < 0))
        pf         = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
        pr         = (avg_win  / avg_loss)    if avg_loss   > 0 else float("inf")

        # Streak analysis
        streak, max_w_streak, max_l_streak, cur_streak = 0, 0, 0, 0
        prev_outcome = None
        for t in reversed(trades):
            outcome = t.get("outcome")
            if outcome == prev_outcome:
                cur_streak += 1
            else:
                if prev_outcome == "win":
                    max_w_streak = max(max_w_streak, cur_streak)
                elif prev_outcome == "loss":
                    max_l_streak = max(max_l_streak, cur_streak)
                cur_streak = 1
                prev_outcome = outcome
        if prev_outcome == "win":
            max_w_streak = max(max_w_streak, cur_streak)
        elif prev_outcome == "loss":
            max_l_streak = max(max_l_streak, cur_streak)

        exp_sign = "+" if expectancy >= 0 else ""
        pf_str   = f"{pf:.2f}" if pf != float("inf") else "∞"

        # Try advanced analytics if research module available
        try:
            from research.analytics.expectancy import edge_ratio  # type: ignore[import]
            edge_note = ""
        except ImportError:
            edge_note = ""

        msg = (
            f"📊 <b>Trade Expectancy — {n} Trades</b>\n\n"
            f"<b>Core Metrics</b>\n"
            f"  Expectancy:    <code>{exp_sign}${expectancy:.4f}</code> per trade\n"
            f"  Win Rate:      <code>{win_rate*100:.1f}%</code>  ({len(wins)}W / {len(losses)}L)\n"
            f"  Profit Factor: <code>{pf_str}</code>  (>1.5 = good)\n"
            f"  Payoff Ratio:  <code>{pr:.2f}</code>  (avg_win/avg_loss)\n\n"
            f"<b>Distribution</b>\n"
            f"  Avg Win:  <code>+${avg_win:.4f}</code>\n"
            f"  Avg Loss: <code>-${avg_loss:.4f}</code>\n\n"
            f"<b>Streaks</b>\n"
            f"  Max Win Streak:  <code>{max_w_streak}</code>\n"
            f"  Max Loss Streak: <code>{max_l_streak}</code>\n\n"
            f"<i>Positive expectancy means the strategy has statistical edge.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Expectancy error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_sharpe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Compute Sharpe, Sortino, and Calmar ratios from trade history."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text("<i>Computing risk-adjusted metrics...</i>", parse_mode="HTML")

    try:
        import math
        trades = _load_blofin_trades()
        if len(trades) < 3:
            await thinking.edit_text(
                "📊 <b>Risk-Adjusted Metrics</b>\n\nNeed at least 3 closed trades. Keep the bot running.",
                parse_mode="HTML",
            )
            return

        returns = [t.get("pnl", 0) for t in trades]
        n       = len(returns)
        mu      = sum(returns) / n
        var     = sum((r - mu) ** 2 for r in returns) / n
        std     = math.sqrt(var) if var > 0 else 1e-9

        # Sharpe (no risk-free rate for simplicity on short trade series)
        sharpe = mu / std if std > 0 else 0.0

        # Sortino (downside deviation only)
        neg     = [r for r in returns if r < 0]
        dd_var  = sum(r ** 2 for r in neg) / n if n > 0 else 1e-9
        dd_std  = math.sqrt(dd_var) if dd_var > 0 else 1e-9
        sortino = mu / dd_std if dd_std > 0 else 0.0

        # Calmar (needs equity curve for drawdown)
        equity = [1000.0]
        for r in returns:
            equity.append(equity[-1] + r)
        peak   = equity[0]
        max_dd = 0.0
        for e in equity:
            peak   = max(peak, e)
            dd     = (peak - e) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        total_ret = (equity[-1] - equity[0]) / equity[0] if equity[0] > 0 else 0.0
        calmar    = (total_ret / max_dd) if max_dd > 0 else float("inf")

        # Omega ratio (threshold = 0)
        gains  = sum(r for r in returns if r > 0)
        losses = abs(sum(r for r in returns if r < 0))
        omega  = (gains / losses) if losses > 0 else float("inf")

        def _fmt(v: float) -> str:
            return f"{v:.3f}" if v != float("inf") else "∞"

        grade_s = "✅ Good" if sharpe > 1.0 else ("⚠️ Marginal" if sharpe > 0 else "❌ Poor")
        grade_o = "✅ Good" if omega  > 1.5 else ("⚠️ Marginal" if omega  > 1.0 else "❌ Poor")

        msg = (
            f"📈 <b>Risk-Adjusted Metrics — {n} Trades</b>\n\n"
            f"<b>Ratios</b>\n"
            f"  Sharpe:  <code>{_fmt(sharpe)}</code>  {grade_s}\n"
            f"  Sortino: <code>{_fmt(sortino)}</code>  (downside-adjusted)\n"
            f"  Calmar:  <code>{_fmt(calmar)}</code>  (return/max_dd)\n"
            f"  Omega:   <code>{_fmt(omega)}</code>   {grade_o}\n\n"
            f"<b>Returns</b>\n"
            f"  Total:   <code>{total_ret*100:+.2f}%</code>\n"
            f"  Avg/Trade: <code>${mu:+.4f}</code>\n"
            f"  Std Dev: <code>${std:.4f}</code>\n\n"
            f"<b>Drawdown</b>\n"
            f"  Max DD:  <code>{max_dd*100:.2f}%</code>\n\n"
            f"<i>Sharpe > 1.0 indicates acceptable risk-adjusted performance.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Sharpe error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full drawdown analysis: max DD, duration, recovery factor."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text("<i>Analysing drawdown...</i>", parse_mode="HTML")

    try:
        trades = _load_blofin_trades()
        if not trades:
            await thinking.edit_text(
                "📉 <b>Drawdown Analysis</b>\n\nNo trade history available.",
                parse_mode="HTML",
            )
            return

        equity = [1000.0]
        for t in trades:
            equity.append(equity[-1] + t.get("pnl", 0))

        # Max drawdown
        peak = equity[0]; max_dd = 0.0; dd_start = 0; dd_end = 0
        current_peak_idx = 0
        for i, e in enumerate(equity):
            if e > peak:
                peak = e; current_peak_idx = i
            dd = (peak - e) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd; dd_start = current_peak_idx; dd_end = i

        # Drawdown duration in trades
        dd_duration = dd_end - dd_start

        # Current drawdown
        cur_peak = max(equity)
        cur_dd   = (cur_peak - equity[-1]) / cur_peak if cur_peak > 0 else 0.0

        # Recovery factor
        total_ret = (equity[-1] - equity[0]) / equity[0] * 100 if equity[0] > 0 else 0.0
        recovery  = (total_ret / (max_dd * 100)) if max_dd > 0 else float("inf")

        # Underwater periods
        in_dd = [i for i in range(len(equity)) if equity[i] < max(equity[:i+1])] if len(equity) > 1 else []
        time_in_dd_pct = len(in_dd) / len(equity) * 100 if equity else 0.0

        dd_grade = "✅ Excellent" if max_dd < 0.05 else ("⚠️ Manageable" if max_dd < 0.15 else "❌ Severe")
        rec_str  = f"{recovery:.2f}" if recovery != float("inf") else "∞"

        msg = (
            f"📉 <b>Drawdown Analysis — {len(trades)} Trades</b>\n\n"
            f"<b>Max Drawdown</b>\n"
            f"  Peak → Trough: <code>{max_dd*100:.2f}%</code>  {dd_grade}\n"
            f"  Duration:      <code>{dd_duration} trades</code>\n"
            f"  Recovery Factor: <code>{rec_str}</code>\n\n"
            f"<b>Current State</b>\n"
            f"  Current DD:    <code>{cur_dd*100:.2f}%</code>\n"
            f"  Time in DD:    <code>{time_in_dd_pct:.1f}%</code> of history\n\n"
            f"<b>Equity</b>\n"
            f"  Start: <code>$1,000.00</code>\n"
            f"  Current: <code>${equity[-1]:.2f}</code>\n"
            f"  Return: <code>{total_ret:+.2f}%</code>\n\n"
            f"<i>Recovery Factor = total_return / max_drawdown. > 2.0 = strong.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Drawdown error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_regime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Classify current market regime for BTC, ETH, SOL."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Classifying market regime...</i>", parse_mode="HTML"
    )

    try:
        from research.regimes.classifier import RegimeClassifier  # type: ignore[import]
        from trading.blofin_exchange import fetch_candles

        clf     = RegimeClassifier()
        symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        lines   = ["🌐 <b>Market Regime Analysis</b>\n"]

        for symbol in symbols:
            try:
                candles_raw = fetch_candles(symbol, "15m", 100)
                from research.types import Candle
                candles = [Candle(**c) for c in candles_raw]
                regime  = clf.classify(candles)

                emoji = {
                    "TRENDING_BULL": "📈", "TRENDING_BEAR": "📉",
                    "RANGING": "↔️", "VOL_EXPANSION": "💥",
                    "VOL_COMPRESSION": "🤐", "MOMENTUM_BULL": "🚀",
                    "MEAN_REVERTING": "🔄", "LIQUIDITY_DROUGHT": "🏜️",
                    "PANIC": "🚨", "UNKNOWN": "❓",
                }.get(regime.label, "❓")

                lines.append(
                    f"{emoji} <b>{symbol}</b>: <code>{regime.label}</code>\n"
                    f"   ADX: <code>{regime.adx:.1f}</code>  "
                    f"RSI: <code>{regime.rsi:.1f}</code>  "
                    f"ATR×: <code>{regime.atr_ratio:.2f}</code>"
                )
            except Exception as e:
                lines.append(f"⚠️ <b>{symbol}</b>: error ({str(e)[:40]})")

        lines.append(
            "\n<i>Regime drives adaptive allocation weights. "
            "PANIC → all strategies throttled.</i>"
        )
        await thinking.edit_text(_truncate("\n".join(lines)), parse_mode="HTML")

    except ImportError:
        await thinking.edit_text(
            "📊 <b>Regime</b>\n\nResearch modules not yet available. "
            "Run the backtesting engine first.",
            parse_mode="HTML",
        )
    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Regime error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_portfolioheat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current strategy weights, allocation, and risk exposure."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Computing portfolio heat...</i>", parse_mode="HTML"
    )

    try:
        import json
        weights_path = Path(__file__).parent.parent / "data" / "blofin_weights.json"
        state_path   = Path(__file__).parent.parent / "data" / "blofin_state.json"

        weights = json.loads(weights_path.read_text()) if weights_path.exists() else {}
        state   = json.loads(state_path.read_text())   if state_path.exists()   else {}

        trade_log     = state.get("trade_log", [])
        open_pos      = state.get("open_positions", []) if hasattr(state, "get") else []
        risk_pct      = state.get("risk_pct", 1.5)
        total_pnl     = state.get("total_pnl", 0.0)
        demo          = state.get("demo_mode", True)

        lines = [
            f"🔥 <b>Portfolio Heat</b>  ({'Demo' if demo else 'Live'})\n",
            f"<b>Risk Settings</b>",
            f"  Base Risk/Trade: <code>{risk_pct}%</code>",
            f"  Total P&L:       <code>${total_pnl:+.4f}</code>",
            f"  Open Positions:  <code>{len(open_pos)}</code>",
            "",
            "<b>Strategy Weights</b>",
        ]

        total_weight = sum(w.get("weight", 1.0) for w in weights.values()) if weights else 4.0
        for name, data in (weights or {}).items():
            w    = data.get("weight", 1.0)
            bar  = "█" * int(w * 5) + "░" * (10 - int(w * 5))
            wr   = data.get("win_rate", 50)
            trd  = data.get("trades", 0)
            lines.append(
                f"  {bar} <code>{name[:14]:<14}</code> "
                f"<code>{w:.2f}×</code>  WR:{wr:.0f}%  T:{trd}"
            )

        if open_pos:
            lines += ["", "<b>Open Positions</b>"]
            for p in open_pos[:5]:
                pnl  = p.get("unrealized_pnl", 0)
                sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"  {p.get('symbol','?')} {p.get('side','?').upper()} "
                    f"[{p.get('strategy','?')[:8]}] P&L: <code>{sign}${pnl:.4f}</code>"
                )

        lines.append("\n<i>Weights auto-adjust after ≥3 trades per strategy.</i>")
        await thinking.edit_text(_truncate("\n".join(lines)), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Portfolio heat error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_montecarlo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run Monte Carlo simulation on trade history."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Running Monte Carlo simulation (1,000 paths)...</i>", parse_mode="HTML"
    )

    try:
        import json, math, random
        trades = _load_blofin_trades()
        if len(trades) < 5:
            await thinking.edit_text(
                "🎲 <b>Monte Carlo</b>\n\nNeed at least 5 closed trades. Keep the bot running.",
                parse_mode="HTML",
            )
            return

        pnls = [t.get("pnl", 0) for t in trades]
        N_SIM = 1000
        N_TRADES = len(pnls)
        INITIAL = 1000.0
        RUIN_THRESHOLD = 0.5   # 50% drawdown

        rng = random.Random(42)
        final_equities = []
        max_drawdowns  = []
        ruin_count     = 0

        for _ in range(N_SIM):
            shuffled = pnls[:]
            rng.shuffle(shuffled)
            eq = INITIAL
            peak = INITIAL
            max_dd = 0.0
            ruined = False
            for pnl in shuffled:
                eq += pnl
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
                if dd >= RUIN_THRESHOLD:
                    ruined = True; break
            if ruined:
                ruin_count += 1
                final_equities.append(INITIAL * (1 - RUIN_THRESHOLD))
            else:
                final_equities.append(eq)
            max_drawdowns.append(max_dd)

        # Compute stats
        final_equities.sort(); max_drawdowns.sort()
        median_eq  = final_equities[N_SIM // 2]
        p5_eq      = final_equities[int(N_SIM * 0.05)]
        p95_eq     = final_equities[int(N_SIM * 0.95)]
        median_ret = (median_eq - INITIAL) / INITIAL * 100
        p5_ret     = (p5_eq    - INITIAL) / INITIAL * 100
        p95_ret    = (p95_eq   - INITIAL) / INITIAL * 100
        median_dd  = max_drawdowns[N_SIM // 2] * 100
        worst_dd   = max_drawdowns[int(N_SIM * 0.95)] * 100
        ruin_prob  = ruin_count / N_SIM * 100
        survivability = sum(1 for e in final_equities if e > INITIAL) / N_SIM * 100

        ruin_grade = "✅ Safe" if ruin_prob < 5 else ("⚠️ Elevated" if ruin_prob < 20 else "❌ Dangerous")

        msg = (
            f"🎲 <b>Monte Carlo — {N_SIM:,} Simulations</b>\n\n"
            f"<b>Return Confidence Interval</b>\n"
            f"  Median:   <code>{median_ret:+.1f}%</code>\n"
            f"  5th pct:  <code>{p5_ret:+.1f}%</code>  (worst case)\n"
            f"  95th pct: <code>{p95_ret:+.1f}%</code>  (best case)\n\n"
            f"<b>Drawdown Distribution</b>\n"
            f"  Median Max DD:  <code>{median_dd:.1f}%</code>\n"
            f"  95th pct DD:    <code>{worst_dd:.1f}%</code>\n\n"
            f"<b>Risk Metrics</b>\n"
            f"  P(Ruin):        <code>{ruin_prob:.1f}%</code>  {ruin_grade}\n"
            f"  Survivability:  <code>{survivability:.1f}%</code>\n\n"
            f"<b>Input</b>\n"
            f"  Trades sampled: <code>{N_TRADES}</code>\n"
            f"  Ruin threshold: <code>50% drawdown</code>\n\n"
            f"<i>MC re-shuffles trade order. P(ruin) < 5% = acceptable.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Monte Carlo error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_executionquality(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show execution quality metrics: slippage, fill efficiency, latency."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Loading execution quality data...</i>", parse_mode="HTML"
    )

    try:
        import json
        eq_path = Path(__file__).parent.parent / "data" / "execution_quality.json"

        if not eq_path.exists():
            await thinking.edit_text(
                "📊 <b>Execution Quality</b>\n\nNo execution data yet. "
                "Requires live trading (not demo mode).",
                parse_mode="HTML",
            )
            return

        try:
            from exchange.execution_quality import ExecutionQualityTracker  # type: ignore[import]
            tracker = ExecutionQualityTracker()
            summary = tracker.summary()
            slippage_by_venue = tracker.slippage_by_venue()
            latency_pct       = tracker.latency_percentiles()
            quality           = tracker.quality_score()
            adverse_rate      = tracker.adverse_selection_rate()
        except ImportError:
            # Fallback: read raw JSON
            data    = json.loads(eq_path.read_text())
            fills   = data.get("fills", [])
            n       = len(fills)
            summary = {
                "avg_slippage_bps": sum(f.get("slippage_bps", 0) for f in fills) / n if n > 0 else 0,
                "avg_latency_ms":   sum(f.get("latency_ms",   0) for f in fills) / n if n > 0 else 0,
                "n_fills":          n,
            }
            slippage_by_venue = {}
            latency_pct       = {}
            quality           = 0.5
            adverse_rate      = 0.0

        n       = summary.get("n_fills", 0)
        avg_sl  = summary.get("avg_slippage_bps", 0)
        avg_lat = summary.get("avg_latency_ms",   0)
        rej     = summary.get("rejection_rate",   0)
        q_grade = "✅" if quality > 0.7 else ("⚠️" if quality > 0.4 else "❌")
        sl_grade = "✅" if avg_sl < 5 else ("⚠️" if avg_sl < 15 else "❌")

        venue_lines = "\n".join(
            f"  {v}: <code>{sl:.2f} bps</code>"
            for v, sl in slippage_by_venue.items()
        ) or "  No venue data"

        lat_lines = (
            f"  p50: <code>{latency_pct.get('p50', 0):.0f}ms</code>  "
            f"p95: <code>{latency_pct.get('p95', 0):.0f}ms</code>  "
            f"p99: <code>{latency_pct.get('p99', 0):.0f}ms</code>"
        ) if latency_pct else "  No latency data"

        msg = (
            f"⚡ <b>Execution Quality — {n} Fills</b>\n\n"
            f"<b>Overall Score</b>\n"
            f"  Quality Score:    <code>{quality:.2f}/1.0</code>  {q_grade}\n\n"
            f"<b>Slippage</b>\n"
            f"  Avg Slippage:     <code>{avg_sl:.2f} bps</code>  {sl_grade}\n"
            f"{venue_lines}\n\n"
            f"<b>Latency</b>\n"
            f"{lat_lines}\n\n"
            f"<b>Fill Quality</b>\n"
            f"  Rejection Rate:   <code>{rej*100:.1f}%</code>\n"
            f"  Adverse Selection: <code>{adverse_rate*100:.1f}%</code>\n\n"
            f"<i>Slippage < 5 bps and latency p95 < 200ms = good execution.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Execution quality error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current liquidity conditions for BTC, ETH, SOL."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Checking liquidity conditions...</i>", parse_mode="HTML"
    )

    try:
        from trading.blofin_exchange import fetch_candles, fetch_ticker

        symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        lines   = ["💧 <b>Liquidity Conditions</b>\n"]

        for sym in symbols:
            try:
                candles_raw = fetch_candles(sym, "15m", 25)
                ticker      = fetch_ticker(sym)

                vols      = [c["volume"] for c in candles_raw]
                avg_vol   = sum(vols[:-1]) / max(len(vols) - 1, 1)
                cur_vol   = vols[-1] if vols else 0
                vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

                closes     = [c["close"] for c in candles_raw]
                change_pct = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0.0

                spread_bps = abs(ticker["ask"] - ticker["bid"]) / ticker["last"] * 10_000 if ticker["last"] > 0 else 0.0

                drought  = vol_ratio < 0.5
                vol_spk  = abs(change_pct) > 3.0
                liq_icon = "🏜️" if drought else ("💥" if vol_spk else "✅")
                vol_icon = "⬇️" if vol_ratio < 0.7 else ("⬆️" if vol_ratio > 1.5 else "→")

                lines.append(
                    f"{liq_icon} <b>{sym}</b>\n"
                    f"   Vol ratio: <code>{vol_ratio:.2f}×</code> {vol_icon}  "
                    f"Spread: <code>{spread_bps:.1f}bps</code>  "
                    f"Move: <code>{change_pct:+.2f}%</code>"
                )
            except Exception as e:
                lines.append(f"⚠️ <b>{sym}</b>: {str(e)[:50]}")

        lines.append("\n<i>Vol ratio < 0.5 = liquidity drought (reduce sizing).</i>")
        await thinking.edit_text(_truncate("\n".join(lines)), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Liquidity error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show slippage analysis from live and simulated execution history."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Analysing slippage...</i>", parse_mode="HTML"
    )

    try:
        import json, math
        eq_path = Path(__file__).parent.parent / "data" / "execution_quality.json"

        if not eq_path.exists():
            # Compute simulated slippage estimate from demo trades
            trades  = _load_blofin_trades()
            n       = len(trades)
            if n == 0:
                await thinking.edit_text(
                    "📊 <b>Slippage</b>\n\nNo trade data. Run the bot first.",
                    parse_mode="HTML"
                )
                return

            # Estimated: BloFin taker fee is 0.06% = 6 bps
            # Typical market slippage: 2–5 bps
            est_sl     = 6.0  # bps (taker fee, dominant cost in demo)
            total_cost = n * est_sl / 10000 * 100  # approx % of notional

            msg = (
                f"📊 <b>Slippage Analysis</b>  (Estimated — Demo Mode)\n\n"
                f"  Trades analysed: <code>{n}</code>\n"
                f"  Est. entry slip: <code>~3–5 bps</code> (BloFin market orders)\n"
                f"  Taker fee:       <code>6 bps</code>  (0.06%)\n"
                f"  Round-trip cost: <code>~14–22 bps</code>\n\n"
                f"<i>Go live to track actual realized slippage per fill.</i>"
            )
            await thinking.edit_text(_truncate(msg), parse_mode="HTML")
            return

        data  = json.loads(eq_path.read_text())
        fills = data.get("fills", [])
        if not fills:
            await thinking.edit_text(
                "📊 <b>Slippage</b>\n\nNo fills recorded yet.",
                parse_mode="HTML"
            )
            return

        slip_vals = [f.get("slippage_bps", 0) for f in fills]
        n         = len(slip_vals)
        avg       = sum(slip_vals) / n
        slip_vals_sorted = sorted(slip_vals)
        p50  = slip_vals_sorted[n // 2]
        p95  = slip_vals_sorted[int(n * 0.95)]
        worst = slip_vals_sorted[-1]
        var  = sum((s - avg) ** 2 for s in slip_vals) / n
        std  = math.sqrt(var) if var > 0 else 0.0

        sl_grade = "✅ Low" if avg < 5 else ("⚠️ Moderate" if avg < 15 else "❌ High")

        msg = (
            f"📊 <b>Slippage Analysis — {n} Fills</b>\n\n"
            f"<b>Distribution</b>\n"
            f"  Average: <code>{avg:.2f} bps</code>  {sl_grade}\n"
            f"  Median:  <code>{p50:.2f} bps</code>\n"
            f"  p95:     <code>{p95:.2f} bps</code>\n"
            f"  Worst:   <code>{worst:.2f} bps</code>\n"
            f"  Std Dev: <code>{std:.2f} bps</code>\n\n"
            f"<b>Cost Impact</b>\n"
            f"  Round-trip est: <code>{avg*2:.2f} bps</code>\n"
            f"  On $1,000 trade: <code>${avg*2/10000*1000:.3f}</code>\n\n"
            f"<i>< 5 bps avg slippage = excellent execution quality.</i>"
        )
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Slippage error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_walkforward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show walk-forward validation results if available."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Loading walk-forward results...</i>", parse_mode="HTML"
    )

    try:
        import json, glob
        wf_path = Path(__file__).parent.parent / "data" / "walkforward"

        if not wf_path.exists() or not list(wf_path.glob("*.json")):
            await thinking.edit_text(
                "🔄 <b>Walk-Forward</b>\n\n"
                "No walk-forward results found.\n\n"
                "Run the optimizer first:\n"
                "<code>from research.walkforward.engine import WalkForwardEngine</code>",
                parse_mode="HTML",
            )
            return

        result_files = sorted(wf_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = json.loads(result_files[0].read_text())

        windows  = result.get("windows", [])
        stab     = result.get("parameter_stability", 0)
        overfit  = result.get("overfit_detected", False)
        oos_m    = result.get("oos_metrics", {})

        stab_grade   = "✅" if stab > 0.7 else ("⚠️" if stab > 0.4 else "❌")
        overfit_flag = "❌ OVERFIT DETECTED" if overfit else "✅ No overfit"

        msg = (
            f"🔄 <b>Walk-Forward Validation</b>\n\n"
            f"<b>Results ({len(windows)} windows)</b>\n"
            f"  Parameter Stability: <code>{stab:.2f}</code>  {stab_grade}\n"
            f"  Overfit Status:      {overfit_flag}\n\n"
        )

        if oos_m:
            msg += (
                f"<b>OOS Performance</b>\n"
                f"  Sharpe:  <code>{oos_m.get('sharpe_ratio', 0):.3f}</code>\n"
                f"  Win Rate: <code>{oos_m.get('win_rate', 0)*100:.1f}%</code>\n"
                f"  Max DD:   <code>{oos_m.get('max_drawdown_pct', 0):.2f}%</code>\n"
                f"  Trades:   <code>{oos_m.get('total_trades', 0)}</code>\n\n"
            )

        msg += "<i>OOS Sharpe > 0.5 and stability > 0.7 = robust strategy.</i>"
        await thinking.edit_text(_truncate(msg), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Walk-forward error: <code>{exc}</code>", parse_mode="HTML"
        )


async def cmd_optimizer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show best known parameters from optimization store."""
    if not is_authorized(update.effective_chat.id):
        return
    thinking = await update.message.reply_text(
        "<i>Loading optimization results...</i>", parse_mode="HTML"
    )

    try:
        import json
        opt_base = Path(__file__).parent.parent / "data" / "optimization"

        if not opt_base.exists() or not any(opt_base.rglob("*.json")):
            await thinking.edit_text(
                "🔧 <b>Optimizer</b>\n\n"
                "No optimization results yet.\n\n"
                "Run an optimization:\n"
                "<code>from research.optimization import ResearchOptimizer</code>",
                parse_mode="HTML",
            )
            return

        lines = ["🔧 <b>Optimization Registry</b>\n"]
        for strategy_dir in sorted(opt_base.iterdir()):
            if not strategy_dir.is_dir():
                continue
            lines.append(f"\n<b>{strategy_dir.name}</b>")
            for pair_file in sorted(strategy_dir.glob("*.json")):
                try:
                    data  = json.loads(pair_file.read_text())
                    best  = data.get("best", {})
                    score = best.get("score", 0)
                    metric = best.get("metric", "?")
                    params = best.get("params", {})
                    ts     = best.get("timestamp", "?")[:10]
                    lines.append(
                        f"  <b>{pair_file.stem}</b>  "
                        f"<code>{metric}={score:.3f}</code>  [{ts}]\n"
                        f"  Params: <code>{str(params)[:60]}</code>"
                    )
                except Exception:
                    lines.append(f"  {pair_file.stem}: unreadable")

        lines.append("\n<i>Load params: ParameterStore().load_best(strategy, symbol)</i>")
        await thinking.edit_text(_truncate("\n".join(lines)), parse_mode="HTML")

    except Exception as exc:
        await thinking.edit_text(
            f"🚨 Optimizer error: <code>{exc}</code>", parse_mode="HTML"
        )


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
    _app.add_handler(CommandHandler("autotrade",       cmd_autotrade))

    # Phase 11: Research & Quant Commands
    _app.add_handler(CommandHandler("expectancy",      cmd_expectancy))
    _app.add_handler(CommandHandler("sharpe",          cmd_sharpe))
    _app.add_handler(CommandHandler("drawdown",        cmd_drawdown))
    _app.add_handler(CommandHandler("regime",          cmd_regime))
    _app.add_handler(CommandHandler("portfolioheat",   cmd_portfolioheat))
    _app.add_handler(CommandHandler("montecarlo",      cmd_montecarlo))
    _app.add_handler(CommandHandler("executionquality", cmd_executionquality))
    _app.add_handler(CommandHandler("liquidity",       cmd_liquidity))
    _app.add_handler(CommandHandler("slippage",        cmd_slippage))
    _app.add_handler(CommandHandler("walkforward",     cmd_walkforward))
    _app.add_handler(CommandHandler("optimizer",       cmd_optimizer))

    _app.add_handler(CommandHandler("stop",      cmd_stop))

    # Free-text conversation (must be last — catches all non-command text)
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🦾 ClawBot v0.6 running.")
    print("   Chat freely or use commands. /help for the full list.")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
