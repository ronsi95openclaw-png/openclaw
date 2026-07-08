"""
Self-Improvement Code Review Agent — ClawBot
=============================================
Scheduled weekly (Sunday 09:00 UTC) — ClawBot reviews its own code,
finds bugs/improvements, and sends a Telegram report.

LLM reads each Python file in the project, analyzes for:
  - Bugs or logic errors
  - Security issues (hardcoded secrets, injection risks)
  - Performance improvements
  - Code quality and maintainability
  - Missing error handling
  - Better approaches for the trading/AI logic

Reports saved to: data/code_reviews/YYYY-MM-DD.md
Telegram command: /codereview
APScheduler: weekly cron, Sunday 09:00 UTC
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("clawbot.agents.code_review")

_PROJECT_ROOT  = Path(__file__).parent.parent
_REVIEWS_DIR   = _PROJECT_ROOT / "data" / "code_reviews"

# Files to review (relative to project root)
_REVIEW_TARGETS = [
    "core/brain.py",
    "core/scheduler.py",
    "trading/strategy.py",
    "trading/trading_strategy.py",
    "trading/exchange.py",
    "trading/executor.py",
    "trading/backtest.py",
    "agents/news_filter_agent.py",
    "agents/sheets_agent.py",
    "content/receiver.py",
]

_MAX_FILE_CHARS = 6000   # truncate large files to avoid token overload


# ---------------------------------------------------------------------------
# LLM review prompt
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """\
You are a senior Python engineer reviewing ClawBot — an automated crypto
trading bot for OpenClaw. Your job is to audit code files and find real,
actionable improvements. Be concise and direct.

For each file review, output:
ISSUES: (bullet list of actual bugs, risks, or problems — be specific)
IMPROVEMENTS: (bullet list of concrete suggestions — with example code where helpful)
PRIORITY: HIGH / MEDIUM / LOW (based on impact to trading performance or stability)

If the file looks good, say so briefly. Don't pad output.
Focus on things that would actually make the bot more profitable, stable, or secure.

## Security Checklist (apply to every file)
- No hardcoded API keys, tokens, or passwords — all secrets via env vars
- No string concatenation in SQL or shell commands (injection risk)
- All user/external inputs validated before use
- Error messages must not leak internal state or stack traces
- No sensitive data (keys, balances, order IDs) written to logs in plaintext

## Trading Agent Security (apply to trading files)
- Prompt injection: external data (news, coin names, webhooks) must NOT enter execution-capable prompts unsanitized
- Spend limits enforced independently from model output — never rely solely on LLM judgment for position sizing
- Circuit breaker: check consecutive losses and hourly PnL drawdown before executing
- Private keys and API secrets come from env vars only, never hardcoded or logged
- All trade decisions audit-logged (not just successful ones)
- Slippage/min_amount_out validated before order send

## Systematic Bug Detection
- Trace data flow for any calculation: follow values from source → transform → output
- Flag any "it worked before" patterns — check if recent changes broke an assumption
- Identify race conditions in async code (shared state, ordering dependencies)
- Check error handling completeness — every external call should have a fallback
"""


def _review_file(filepath: str, content: str) -> str:
    """Ask LLM to review a single file. Returns review text."""
    prompt = (
        f"Review this file from ClawBot: `{filepath}`\n\n"
        f"```python\n{content[:_MAX_FILE_CHARS]}"
        f"{'... [truncated]' if len(content) > _MAX_FILE_CHARS else ''}\n```\n\n"
        f"Analyze for bugs, security issues, performance problems, and improvements."
    )

    # Try Ollama first
    try:
        from ollama import chat as ollama_chat
        model = os.getenv("OLLAMA_MODEL", "gemma3")
        resp  = ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": _REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.message.content.strip()
    except Exception as e:
        logger.warning(f"Ollama failed for {filepath}: {e}")

    # Haiku fallback
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return f"LLM unavailable — skipped {filepath}"
        client = anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=_REVIEW_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception as e2:
        return f"Review failed: {e2}"


def _generate_summary(file_reviews: list[dict]) -> str:
    """Ask LLM to synthesize all file reviews into a top-level action plan."""
    high_priority = [r for r in file_reviews if "HIGH" in r.get("review", "")]
    summary_input = "\n\n".join(
        f"=== {r['file']} ===\n{r['review'][:500]}"
        for r in high_priority[:5]   # top 5 high-priority issues
    )

    prompt = (
        f"Here are the HIGH priority issues found in ClawBot's codebase:\n\n"
        f"{summary_input}\n\n"
        f"Generate a concise action plan (max 5 items) ranked by impact. "
        f"Format as numbered list. Focus on what to fix first."
    )

    try:
        from ollama import chat as ollama_chat
        model = os.getenv("OLLAMA_MODEL", "gemma3")
        resp  = ollama_chat(
            model=model,
            messages=[
                {"role": "system", "content": "You are a CTO reviewing an AI trading bot codebase. Be direct and prioritize by business impact."},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.message.content.strip()
    except Exception:
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return "LLM unavailable for summary."
            client = anthropic.Anthropic(api_key=api_key)
            resp   = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return next((b.text for b in resp.content if b.type == "text"), "").strip()
        except Exception as e:
            return f"Summary generation failed: {e}"


# ---------------------------------------------------------------------------
# Main review runner
# ---------------------------------------------------------------------------

async def run_code_review(bot=None, chat_id: int = 0) -> str:
    """
    Full pipeline:
      1. Read each target file
      2. LLM reviews each file
      3. LLM synthesizes top action items
      4. Save markdown report to data/code_reviews/
      5. Send Telegram notification

    Can be called from /codereview command or APScheduler.
    """
    _REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    now     = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d")

    if bot and chat_id:
        await bot.send_message(
            chat_id=chat_id,
            text="🔍 <b>ClawBot Self-Review Starting...</b>\n<i>Reviewing code files with AI. This takes 2-5 min.</i>",
            parse_mode="HTML",
        )

    file_reviews: list[dict] = []

    for rel_path in _REVIEW_TARGETS:
        filepath = _PROJECT_ROOT / rel_path
        if not filepath.exists():
            logger.info(f"Skipping (not found): {rel_path}")
            continue

        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error(f"Could not read {rel_path}: {exc}")
            continue

        logger.info(f"Reviewing {rel_path}...")
        review = _review_file(rel_path, content)
        file_reviews.append({
            "file": rel_path,
            "lines": len(content.splitlines()),
            "review": review,
        })

    if not file_reviews:
        msg = "❌ No files found to review."
        if bot and chat_id:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        return msg

    # Generate overall action plan
    action_plan = _generate_summary(file_reviews)

    # Count priorities
    high_count   = sum(1 for r in file_reviews if "HIGH" in r["review"])
    medium_count = sum(1 for r in file_reviews if "MEDIUM" in r["review"])

    # Save markdown report
    report_path = _REVIEWS_DIR / f"{now_str}.md"
    lines = [
        f"# ClawBot Code Review — {now_str}\n",
        f"**Files reviewed:** {len(file_reviews)} | "
        f"**HIGH priority:** {high_count} | **MEDIUM:** {medium_count}\n",
        f"## Priority Action Plan\n",
        f"{action_plan}\n",
        "---\n",
        "## Per-File Reviews\n",
    ]
    for r in file_reviews:
        lines.append(f"### `{r['file']}` ({r['lines']} lines)\n")
        lines.append(r["review"] + "\n\n")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Code review saved: {report_path}")

    # Format Telegram message
    action_preview = action_plan[:600] + "..." if len(action_plan) > 600 else action_plan
    telegram_msg = (
        f"🔍 <b>ClawBot Weekly Code Review</b>\n"
        f"<i>{now_str} — {len(file_reviews)} files reviewed</i>\n\n"
        f"⚠️ HIGH priority issues: <b>{high_count}</b>\n"
        f"📋 MEDIUM priority: <b>{medium_count}</b>\n\n"
        f"<b>🎯 Top Action Items:</b>\n"
        f"<i>{action_preview}</i>\n\n"
        f"<i>Full report: data/code_reviews/{now_str}.md</i>"
    )

    if bot and chat_id:
        await bot.send_message(chat_id=chat_id, text=telegram_msg, parse_mode="HTML")

    return telegram_msg


# ---------------------------------------------------------------------------
# APScheduler wire-up
# ---------------------------------------------------------------------------

def schedule_weekly_review(scheduler, bot, chat_id: int) -> None:
    """
    Register weekly code review job in APScheduler.
    Runs every Sunday at 09:00 UTC.

    Call this from core/scheduler.py start_scheduler() or after bot startup.

    Example wire-up in core/scheduler.py:
        from agents.code_review_agent import schedule_weekly_review
        schedule_weekly_review(_scheduler, bot, chat_id)
    """
    job_id = "weekly_code_review"

    # Remove existing job if present
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    scheduler.add_job(
        run_code_review,
        trigger="cron",
        day_of_week="sun",
        hour=9,
        minute=0,
        id=job_id,
        kwargs={"bot": bot, "chat_id": chat_id},
        replace_existing=True,
    )
    logger.info(f"Scheduled weekly code review: Sundays 09:00 UTC (job_id={job_id})")


def get_last_review_summary() -> Optional[dict]:
    """Return metadata about the most recent code review."""
    if not _REVIEWS_DIR.exists():
        return None
    reports = sorted(_REVIEWS_DIR.glob("*.md"), reverse=True)
    if not reports:
        return None
    latest = reports[0]
    content = latest.read_text(encoding="utf-8")
    lines = content.splitlines()
    return {
        "date": latest.stem,
        "file": str(latest),
        "preview": "\n".join(lines[:10]),
    }
