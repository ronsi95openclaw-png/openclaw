"""
News Filter Agent — ClawBot
============================
Blocks or allows trading during high-impact macro news events.

Macro calendar hardcoded (ET → UTC):
  - CPI:  2nd Tue/month  08:30 ET = 13:30 UTC  ±30 min block
  - FOMC: ~8x/year       14:00 ET = 19:00 UTC  ±30 min block
  - NFP:  1st Fri/month  08:30 ET = 13:30 UTC  ±30 min block
  - PCE:  last Fri/month 08:30 ET = 13:30 UTC  ±30 min block
  - GDP:  last Wed/month 08:30 ET = 13:30 UTC  ±30 min block

LLM reasons from datetime context — Ollama first, Haiku fallback.
Returns structured JSON: decision, reason, event_detected, block_window.

APScheduler usage (wire into scheduler.py):
  scheduler.add_job(check_and_alert, "interval", minutes=15, id="news_filter")
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("clawbot.agents.news_filter")

# ---------------------------------------------------------------------------
# Macro calendar — week_of_month + weekday + time_utc
# ---------------------------------------------------------------------------

_MACRO_EVENTS = [
    {
        "name": "CPI Release",
        "weekday": 1,          # Tuesday (0=Mon)
        "week_of_month": 2,    # 2nd Tuesday
        "time_utc": (13, 30),
        "window_minutes": 30,
    },
    {
        "name": "NFP (Non-Farm Payrolls)",
        "weekday": 4,          # Friday
        "week_of_month": 1,    # 1st Friday
        "time_utc": (13, 30),
        "window_minutes": 30,
    },
    {
        "name": "PCE Inflation",
        "weekday": 4,          # Friday
        "week_of_month": -1,   # last Friday
        "time_utc": (13, 30),
        "window_minutes": 30,
    },
    {
        "name": "GDP Release",
        "weekday": 2,          # Wednesday
        "week_of_month": -1,   # last Wednesday
        "time_utc": (13, 30),
        "window_minutes": 30,
    },
    {
        "name": "FOMC Decision",
        "weekday": 2,          # Wednesday
        "week_of_month": None, # variable — LLM handles reasoning
        "time_utc": (19, 0),
        "window_minutes": 30,
        "notes": "~8x per year, typically 2nd or 3rd Wed of FOMC months: Jan,Mar,May,Jun,Jul,Sep,Nov,Dec",
    },
]

_FOMC_MONTHS = {1, 3, 5, 6, 7, 9, 11, 12}


# ---------------------------------------------------------------------------
# Calendar detection (pure Python — fast path before LLM)
# ---------------------------------------------------------------------------

def _week_of_month(dt: datetime) -> int:
    """Return which occurrence of this weekday in the month (1-based)."""
    day = dt.day
    return (day - 1) // 7 + 1


def _last_week_of_month(dt: datetime) -> bool:
    """True if dt is in the last 7 days of the month."""
    import calendar
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return dt.day > last_day - 7


def _is_near_event(now_utc: datetime) -> Optional[dict]:
    """Check if now is within any event block window. Returns event dict or None."""
    weekday = now_utc.weekday()
    week = _week_of_month(now_utc)
    month = now_utc.month

    for event in _MACRO_EVENTS:
        if event["weekday"] != weekday:
            continue

        # Week-of-month match
        wom = event["week_of_month"]
        if wom == -1 and not _last_week_of_month(now_utc):
            continue
        if wom is not None and wom != -1 and wom != week:
            continue

        # FOMC month check
        if event["name"] == "FOMC Decision" and month not in _FOMC_MONTHS:
            continue

        # Time window check
        h, m = event["time_utc"]
        event_time = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
        window = timedelta(minutes=event["window_minutes"])
        if abs(now_utc - event_time) <= window:
            return {
                "event": event["name"],
                "event_time_utc": event_time.strftime("%Y-%m-%d %H:%M UTC"),
                "window_minutes": event["window_minutes"],
            }

    return None


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

_NEWS_SYSTEM = """\
You are a macro-aware crypto trading filter for ClawBot (OpenClaw).

Your job: decide if trading should be BLOCKED or ALLOWED right now based on the
current UTC datetime and the macro economic event calendar.

MACRO EVENTS that BLOCK trading (±30 min around event time):
- CPI:   2nd Tuesday of month, 13:30 UTC
- NFP:   1st Friday of month,  13:30 UTC
- PCE:   last Friday of month, 13:30 UTC
- GDP:   last Wednesday of month, 13:30 UTC
- FOMC:  ~8x/year (Jan,Mar,May,Jun,Jul,Sep,Nov,Dec), Wednesdays, 19:00 UTC

Always respond with valid JSON only. No extra text.
Format:
{
  "decision": "BLOCK" or "ALLOW",
  "reason": "one sentence explanation",
  "event_detected": "event name or null",
  "block_window": "HH:MM - HH:MM UTC or null"
}
"""


# ---------------------------------------------------------------------------
# LLM call — Ollama first, Haiku fallback
# ---------------------------------------------------------------------------

def _ask_llm(prompt: str) -> dict:
    """Ask LLM and parse JSON response."""
    raw = ""
    try:
        from ollama import chat as ollama_chat
        model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        response = ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": _NEWS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.message.content.strip()
    except Exception as e:
        logger.warning(f"Ollama failed, trying Haiku: {e}")
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("No Anthropic API key")
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                system=_NEWS_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = next((b.text for b in resp.content if b.type == "text"), "").strip()
        except Exception as e2:
            logger.error(f"Both LLMs failed: {e2}")
            return {"decision": "ALLOW", "reason": "LLM unavailable — defaulting to ALLOW", "event_detected": None, "block_window": None}

    # Parse JSON from response
    try:
        # Strip markdown code fences if present
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.error(f"Could not parse LLM JSON: {raw}")
        return {"decision": "ALLOW", "reason": "Parse error — defaulting to ALLOW", "event_detected": None, "block_window": None}


# ---------------------------------------------------------------------------
# Main public interface
# ---------------------------------------------------------------------------

def check_news_filter(now_utc: Optional[datetime] = None) -> dict:
    """
    Check if trading should be blocked based on macro news calendar.

    Returns dict with keys: decision, reason, event_detected, block_window

    Fast path: pure Python calendar check (no LLM call).
    LLM path: used when fast path is uncertain (e.g. FOMC month but unclear week).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Fast path — deterministic calendar check
    event = _is_near_event(now_utc)
    if event:
        h, m = divmod(event["window_minutes"], 60)
        event_dt = datetime.strptime(event["event_time_utc"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        window_start = (event_dt - timedelta(minutes=event["window_minutes"])).strftime("%H:%M")
        window_end = (event_dt + timedelta(minutes=event["window_minutes"])).strftime("%H:%M")
        return {
            "decision": "BLOCK",
            "reason": f"{event['event']} detected — blocking trades ±{event['window_minutes']} min",
            "event_detected": event["event"],
            "block_window": f"{window_start} - {window_end} UTC",
        }

    # LLM confirmation for ambiguous cases (FOMC month, near boundaries)
    weekday_name = now_utc.strftime("%A")
    week = _week_of_month(now_utc)
    prompt = (
        f"Current UTC datetime: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Day: {weekday_name}, week {week} of the month\n"
        f"Month: {now_utc.strftime('%B %Y')}\n\n"
        f"Is there a major macro event within ±30 minutes? "
        f"Should trading be BLOCKED or ALLOWED right now?"
    )

    result = _ask_llm(prompt)
    result.setdefault("decision", "ALLOW")
    result.setdefault("event_detected", None)
    result.setdefault("block_window", None)
    result.setdefault("reason", "")
    return result


# ---------------------------------------------------------------------------
# Telegram formatter
# ---------------------------------------------------------------------------

def format_telegram_message(result: dict, now_utc: Optional[datetime] = None) -> str:
    """Format check_news_filter() result as Telegram HTML message."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    decision = result.get("decision", "ALLOW")
    icon = "🚫" if decision == "BLOCK" else "✅"
    event = result.get("event_detected") or "None"
    window = result.get("block_window") or "—"
    reason = result.get("reason", "")

    return (
        f"{icon} <b>News Filter: {decision}</b>\n\n"
        f"🕐 <b>Time:</b> {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"📰 <b>Event:</b> {event}\n"
        f"⏱ <b>Block Window:</b> {window}\n\n"
        f"💬 {reason}"
    )


# ---------------------------------------------------------------------------
# APScheduler job — wire into scheduler.py
# ---------------------------------------------------------------------------

async def check_and_alert(bot, chat_id: int) -> None:
    """
    APScheduler async job. Call every 15 min.
    Only sends Telegram alert when BLOCK is triggered.

    Wire-up in core/scheduler.py:
        from agents.news_filter_agent import check_and_alert
        scheduler.add_job(
            check_and_alert, "interval", minutes=15,
            id="news_filter", args=[bot, chat_id]
        )
    """
    result = check_news_filter()
    if result["decision"] == "BLOCK":
        msg = format_telegram_message(result)
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        logger.info(f"News BLOCK alert sent: {result['event_detected']}")
