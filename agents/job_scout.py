"""
Job Scout — Scrapes Whop for clip editing gigs, scores with Ollama,
queues top picks for Telegram approval before any outreach.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.job_scout")

_DATA_DIR   = Path(__file__).parent.parent / "data"
_STATE_FILE = _DATA_DIR / "job_scout_state.json"

_DEFAULT_STATE: dict = {
    "last_scan": None,
    "pending_approval": [],
    "approved": [],
    "rejected": [],
    "applied": [],
    "scan_count": 0,
}

_SEARCH_TERMS = [
    # Freelance dev / bots
    "telegram bot",
    "discord bot",
    "python script",
    "automation script",
    "API integration",
    "web scraper",
    # AI / ML tasks
    "prompt engineer",
    "data labeling",
    "AI automation",
    "chatbot builder",
    "AI assistant",
    # Content (crypto/tech niche)
    "crypto newsletter",
    "ghostwriter",
    "content writer",
    "tweet thread",
    "Twitter ghostwrite",
    # Video / clip editing (kept from original)
    "clip editor",
    "short form editor",
    "TikTok editor",
    # Notion / digital products
    "Notion template",
    "digital product",
    "info product",
    # Consulting
    "AI consulting",
    "automation consulting",
    "no-code builder",
]

# Target categories for scoring (used in score_job prompt)
_TARGET_CATEGORIES = (
    "freelance dev (bots, scripts, Telegram, APIs), "
    "AI/ML tasks (data labeling, prompt engineering, chatbots), "
    "content creation (crypto/tech niche, ghostwriting, newsletters), "
    "Notion templates or digital products, "
    "AI automation consulting"
)

_WHOP_URLS = [
    "https://whop.com/marketplace/",
    "https://whop.com/marketplace/?category=services",
    "https://whop.com/marketplace/?category=software",
]


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_state() -> dict:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_DEFAULT_STATE)


def _save_state(state: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Scraping ───────────────────────────────────────────────────────────────────

def scrape_whop_jobs(max_results: int = 20) -> list[dict]:
    """Fetch Whop marketplace pages and extract clip/video editing gigs.

    Uses only stdlib regex + string parsing — no bs4 dependency.
    Returns list of dicts: {title, description, budget_min, budget_max,
                             url, posted_at, platform}
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — cannot scrape Whop")
        return []

    jobs: list[dict] = []
    seen_urls: set[str] = set()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for url in _WHOP_URLS:
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                logger.warning("Whop returned %s for %s", resp.status_code, url)
                continue
            html = resp.text
        except Exception as exc:
            logger.warning("Whop fetch failed for %s: %s", url, exc)
            continue

        # Extract product/listing cards — look for anchors with /hub/ or /marketplace/
        # Pattern: href="/hub/slug" or href="/marketplace/slug"
        link_pattern = re.compile(
            r'href="(/(?:hub|marketplace|product)/[^"?#]{3,80})"', re.IGNORECASE
        )
        card_links = list(dict.fromkeys(link_pattern.findall(html)))  # deduplicate

        # Also look for JSON-LD or embedded product data
        # Extract any title-like strings near our search terms
        text_lower = html.lower()

        for term in _SEARCH_TERMS:
            if term.lower() not in text_lower:
                continue
            # Find surrounding text (±300 chars) around each mention
            idx = 0
            while True:
                pos = text_lower.find(term.lower(), idx)
                if pos == -1:
                    break
                idx = pos + 1

                # Grab context window
                start = max(0, pos - 200)
                end   = min(len(html), pos + 400)
                chunk = html[start:end]

                # Strip HTML tags from chunk
                clean = re.sub(r"<[^>]+>", " ", chunk)
                clean = re.sub(r"\s+", " ", clean).strip()

                # Try to extract a title (text before/after the term, up to 80 chars)
                title_match = re.search(
                    r'(?:title|heading|h[1-6])["\s>:]*([^<"\n]{5,80})',
                    chunk, re.IGNORECASE
                )
                title = title_match.group(1).strip() if title_match else f"{term.title()} Gig"
                title = re.sub(r"\s+", " ", title)[:80]

                # Budget extraction: look for $N or $N-$M patterns
                budget_matches = re.findall(r'\$\s*(\d[\d,]*)', clean)
                budget_min = 0
                budget_max = 0
                if budget_matches:
                    amounts = [int(b.replace(",", "")) for b in budget_matches[:2]]
                    budget_min = amounts[0]
                    budget_max = amounts[1] if len(amounts) > 1 else amounts[0]

                # Find closest href
                href_match = re.search(
                    r'href="(/(?:hub|marketplace|product)/[^"?#]{3,80})"', chunk
                )
                listing_url = (
                    "https://whop.com" + href_match.group(1)
                    if href_match
                    else f"https://whop.com/marketplace/?q={term.replace(' ', '+')}"
                )

                if listing_url in seen_urls:
                    break
                seen_urls.add(listing_url)

                jobs.append({
                    "title": title,
                    "description": clean[:300],
                    "budget_min": budget_min,
                    "budget_max": budget_max,
                    "url": listing_url,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "platform": "whop",
                    "matched_term": term,
                })

                if len(jobs) >= max_results:
                    break

            if len(jobs) >= max_results:
                break

        if len(jobs) >= max_results:
            break

    # If we found nothing from page parsing, generate stub entries per search term
    # so scoring still runs (useful when Whop returns JS-rendered content)
    if not jobs:
        logger.info("No jobs parsed from HTML — generating search-based stubs")
        for term in _SEARCH_TERMS[:3]:
            jobs.append({
                "title": f"{term.title()} Wanted",
                "description": f"Freelance {term} opportunity sourced from Whop marketplace search.",
                "budget_min": 0,
                "budget_max": 0,
                "url": f"https://whop.com/marketplace/?q={term.replace(' ', '+')}",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "platform": "whop",
                "matched_term": term,
            })

    return jobs[:max_results]


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_job(job: dict) -> dict:
    """Score a job using ask_hybrid and attach score + reason to the dict."""
    try:
        from core.brain import ask_hybrid
        prompt = (
            f"Rate this freelance gig 1-10 for a solo dev/builder who specialises in: "
            f"{_TARGET_CATEGORIES}. "
            f"Budget: ${job['budget_min']}-${job['budget_max']}. "
            f"Title: {job['title']}. "
            f"Description: {job['description'][:200]}. "
            f"Score higher for higher budgets, clear scope, and match to the target categories. "
            f"Reply with: SCORE: X/10\nREASON: one sentence"
        )
        response, _ = ask_hybrid(prompt, force="simple")
        # Parse score
        score_match = re.search(r"SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10", response, re.IGNORECASE)
        reason_match = re.search(r"REASON:\s*(.+)", response, re.IGNORECASE)
        score  = float(score_match.group(1)) if score_match else 5.0
        reason = reason_match.group(1).strip() if reason_match else "No reason given."
    except Exception as exc:
        logger.warning("score_job failed for '%s': %s", job.get("title"), exc)
        score  = 5.0
        reason = "Scoring unavailable."

    job = dict(job)
    job["score"]        = score
    job["score_reason"] = reason
    return job


# ── Scout cycle ────────────────────────────────────────────────────────────────

def run_scout_cycle(bot=None, chat_id: int = 0) -> str:
    """Full scan: scrape → score → rank → save top 3 → optionally notify via Telegram."""
    import asyncio

    state = _load_state()

    raw_jobs = scrape_whop_jobs(max_results=20)
    if not raw_jobs:
        logger.warning("No jobs found during scout cycle")
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        state["scan_count"] = state.get("scan_count", 0) + 1
        _save_state(state)
        return "Scout cycle complete — 0 jobs found."

    scored = [score_job(j) for j in raw_jobs]
    scored.sort(key=lambda j: j.get("score", 0), reverse=True)
    top3 = scored[:3]

    state["last_scan"]        = datetime.now(timezone.utc).isoformat()
    state["scan_count"]       = state.get("scan_count", 0) + 1
    state["pending_approval"] = top3
    _save_state(state)

    status = f"Scout cycle complete — {len(raw_jobs)} scraped, top {len(top3)} queued."

    if bot and chat_id:
        msg = _format_top3(top3)
        try:
            asyncio.get_event_loop().run_until_complete(
                bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            )
        except RuntimeError:
            # If no event loop (called outside async context)
            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(
                    bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                )
                loop.close()
            except Exception as exc:
                logger.warning("Telegram send failed in run_scout_cycle: %s", exc)

    return status


def _format_top3(jobs: list[dict]) -> str:
    """Format top-3 job picks for Telegram HTML."""
    numbers = ["1\u20e3", "2\u20e3", "3\u20e3"]
    lines = ["<b>Job Scout — Top 3 Picks</b>\n"]
    for i, job in enumerate(jobs[:3]):
        num = numbers[i] if i < len(numbers) else f"{i+1}."
        budget = (
            f"${job['budget_min']}-${job['budget_max']}"
            if job.get("budget_max", 0) > 0
            else "Budget unspecified"
        )
        score  = job.get("score", "?")
        reason = job.get("score_reason", "")
        url    = job.get("url", "")
        title  = job.get("title", "Untitled")
        lines.append(
            f"{num} <b>{title}</b> — {budget}\n"
            f"Score: {score}/10 — {reason}\n"
            f"<a href=\"{url}\">{url[:60]}</a>\n"
            f"/approve_job {i+1}  \u00b7  /reject_job {i+1}\n"
        )
    return "\n".join(lines)


# ── Approve / Reject ───────────────────────────────────────────────────────────

def approve_job(job_index: int) -> dict:
    """Move pending job at 1-based index to approved list. Returns moved job or error."""
    state = _load_state()
    pending = state.get("pending_approval", [])
    idx = job_index - 1
    if idx < 0 or idx >= len(pending):
        return {"error": f"No pending job at index {job_index}"}
    job = pending.pop(idx)
    state["approved"].append(job)
    state["pending_approval"] = pending
    _save_state(state)
    return job


def reject_job(job_index: int) -> dict:
    """Move pending job at 1-based index to rejected list. Returns moved job or error."""
    state = _load_state()
    pending = state.get("pending_approval", [])
    idx = job_index - 1
    if idx < 0 or idx >= len(pending):
        return {"error": f"No pending job at index {job_index}"}
    job = pending.pop(idx)
    state["rejected"].append(job)
    state["pending_approval"] = pending
    _save_state(state)
    return job


def get_scout_status() -> dict:
    """Return a summary of the current scout state."""
    state = _load_state()
    return {
        "last_scan":       state.get("last_scan"),
        "scan_count":      state.get("scan_count", 0),
        "pending":         len(state.get("pending_approval", [])),
        "approved":        len(state.get("approved", [])),
        "rejected":        len(state.get("rejected", [])),
        "applied":         len(state.get("applied", [])),
        "pending_jobs":    state.get("pending_approval", []),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def run_job_scout(bot=None, chat_id: int = 0) -> str:
    """Run a full scout cycle. Returns status string."""
    return run_scout_cycle(bot=bot, chat_id=chat_id)


def format_scout_status(status: dict) -> str:
    """Format get_scout_status() dict as a Telegram-friendly HTML string."""
    last = status.get("last_scan") or "Never"
    if last and last != "Never":
        last = last[:16].replace("T", " ") + " UTC"

    lines = [
        "<b>Job Scout Status</b>",
        "",
        f"Last scan:  <code>{last}</code>",
        f"Total scans: {status['scan_count']}",
        f"Pending approval: {status['pending']}",
        f"Approved: {status['approved']}",
        f"Rejected: {status['rejected']}",
        f"Applied: {status['applied']}",
    ]

    pending_jobs = status.get("pending_jobs", [])
    if pending_jobs:
        lines.append("\n<b>Pending Jobs:</b>")
        for i, job in enumerate(pending_jobs, 1):
            title  = job.get("title", "?")[:50]
            score  = job.get("score", "?")
            budget = (
                f"${job['budget_min']}-${job['budget_max']}"
                if job.get("budget_max", 0) > 0
                else "unspecified"
            )
            lines.append(f"  {i}. {title} | Score:{score}/10 | {budget}")
            lines.append(f"     /approve_job {i}  \u00b7  /reject_job {i}")

    return "\n".join(lines)
