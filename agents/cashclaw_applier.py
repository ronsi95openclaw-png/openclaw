"""CashClaw Applier — Outreach generator and application sender.

Takes an approved job from the Job Scout queue, runs it through HumanVoice,
and sends the humanized pitch to Telegram for Ronnie's final review before
any real outreach is sent. Nothing auto-fires without explicit /send_apply N.

Pipeline:
    approved job → generate_outreach() → Telegram preview → /send_apply N

State is stored in data/applier_state.json alongside the job_scout_state.json.

Public API:
    generate_apply(job_index: int) -> dict
        Pull approved job at 1-based index, run HumanVoice, return result.

    confirm_apply(draft_index: int) -> dict
        Mark a draft as sent (logs it, moves to applied list in scout state).

    get_applier_status() -> dict

    format_apply_preview(result: dict) -> str
        Telegram-safe HTML for the draft preview message.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("openclaw.cashclaw_applier")

_DATA_DIR   = Path(__file__).parent.parent / "data"
_STATE_FILE = _DATA_DIR / "applier_state.json"

_DEFAULT_STATE: dict = {
    "drafts":  [],   # pending Telegram previews
    "sent":    [],   # confirmed/sent
    "total_generated": 0,
    "total_sent":      0,
}


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


# ── Quality gate ──────────────────────────────────────────────────────────────

def _quality_gate(job: dict) -> tuple[bool, str]:
    """
    Pre-outreach quality check. Returns (passes: bool, reason: str).

    Checks:
    1. failure_memory — any logged failures for this platform
    2. Scout rejected history — similar title rejected before
    3. Basic sanity: title must be non-empty, not a stub
    """
    title    = (job.get("title") or "").strip()
    platform = (job.get("platform") or "").lower()
    url      = (job.get("url") or "")

    # Sanity: stub titles from scraper fallback
    stub_patterns = ["gig wanted", "gig opportunity", "untitled"]
    if not title or any(p in title.lower() for p in stub_patterns):
        return False, f"Stub/empty title: '{title}'"

    # Check failure_memory for platform failures
    try:
        from agents.failure_memory import get_lessons
        lessons = get_lessons(query=platform, limit=5)
        platform_failures = [l for l in lessons if "cashclaw" in " ".join(l.get("tags", [])) or platform in l.get("error", "").lower()]
        if len(platform_failures) >= 3:
            return False, f"Platform '{platform}' has {len(platform_failures)} logged failures — skipping"
    except Exception:
        pass

    # Check scout rejected history for similar title
    try:
        from pathlib import Path
        import json
        state_file = Path(__file__).parent.parent / "data" / "job_scout_state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            rejected = state.get("rejected", [])
            # Simple similarity: first 3 words of title match
            title_words = set(title.lower().split()[:3])
            for rj in rejected[-20:]:  # check last 20 rejections
                rj_title = (rj.get("title") or "").lower()
                rj_words = set(rj_title.split()[:3])
                overlap = title_words & rj_words
                if len(overlap) >= 2:
                    return False, f"Similar title previously rejected: '{rj_title[:50]}'"
    except Exception:
        pass

    return True, "OK"


# ── Core functions ─────────────────────────────────────────────────────────────

def generate_apply(job_index: int, style: str = "cold") -> dict:
    """Generate a humanized outreach draft for approved job at 1-based job_index.

    Pulls from job_scout approved list, runs HumanVoice, stores draft.
    Returns the full draft result dict (or error dict).
    """
    # Load approved jobs from scout state
    scout_state_file = _DATA_DIR / "job_scout_state.json"
    if not scout_state_file.exists():
        return {"error": "No job scout state found. Run /scout first."}

    try:
        scout_state = json.loads(scout_state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"Could not read job scout state: {exc}"}

    approved = scout_state.get("approved", [])
    idx = job_index - 1
    if idx < 0 or idx >= len(approved):
        return {
            "error": (
                f"No approved job at index {job_index}. "
                f"There are {len(approved)} approved jobs. "
                f"Use /approve_job N first."
            )
        }

    job = approved[idx]

    # Quality gate — check before spending Haiku tokens
    passes, reason = _quality_gate(job)
    if not passes:
        # Log to failure_memory
        try:
            from agents.failure_memory import log_lesson
            log_lesson(
                error=f"CashClaw quality gate blocked outreach for '{job.get('title','?')}'",
                fix=f"Reason: {reason}. Review job quality before approving.",
                file="agents/cashclaw_applier.py",
                tags=["cashclaw", "quality-gate", job.get("platform", "unknown")],
            )
        except Exception:
            pass
        return {"error": f"Quality gate blocked: {reason}", "job_title": job.get("title", "?")}

    # Run HumanVoice
    try:
        from agents.human_voice import generate_outreach
        result = generate_outreach(job, style=style)
    except Exception as exc:
        logger.error("generate_outreach failed: %s", exc, exc_info=True)
        return {"error": f"HumanVoice failed: {exc}"}

    # Store draft
    state = _load_state()
    draft_entry = {
        "draft_index":  len(state["drafts"]) + 1,
        "job_index":    job_index,
        "job":          job,
        "style":        style,
        "draft_raw":    result["draft_raw"],
        "draft_final":  result["draft_final"],
        "violations":   result.get("violations", []),
        "model_used":   result.get("model_used", "?"),
        "tokens_used":  result.get("tokens_used", 0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status":       "pending",
    }
    state["drafts"].append(draft_entry)
    state["total_generated"] = state.get("total_generated", 0) + 1
    _save_state(state)

    return draft_entry


def confirm_apply(draft_index: int) -> dict:
    """Mark a draft as sent. Moves it to sent list, updates scout applied list.

    This is the confirmation that Ronnie actually sent the outreach.
    Call this AFTER you've manually sent the message on Whop/Discord/etc.
    """
    state = _load_state()
    drafts = state.get("drafts", [])

    target = None
    for d in drafts:
        if d.get("draft_index") == draft_index and d.get("status") == "pending":
            target = d
            break

    if not target:
        return {"error": f"No pending draft at index {draft_index}"}

    target["status"]  = "sent"
    target["sent_at"] = datetime.now(timezone.utc).isoformat()

    state["sent"] = state.get("sent", []) + [target]
    state["drafts"] = [d for d in drafts if d.get("draft_index") != draft_index]
    state["total_sent"] = state.get("total_sent", 0) + 1
    _save_state(state)

    # Also update job_scout state: move the job to "applied"
    scout_state_file = _DATA_DIR / "job_scout_state.json"
    try:
        if scout_state_file.exists():
            scout_state = json.loads(scout_state_file.read_text(encoding="utf-8"))
            job_idx = target.get("job_index", 1) - 1
            approved = scout_state.get("approved", [])
            if 0 <= job_idx < len(approved):
                job = approved.pop(job_idx)
                job["applied_at"] = target["sent_at"]
                scout_state["applied"] = scout_state.get("applied", []) + [job]
                scout_state["approved"] = approved
                scout_state_file.write_text(
                    json.dumps(scout_state, indent=2), encoding="utf-8"
                )
    except Exception as exc:
        logger.warning("Could not update scout state after confirm: %s", exc)

    return target


def discard_draft(draft_index: int) -> dict:
    """Discard a pending draft without sending."""
    state = _load_state()
    drafts = state.get("drafts", [])
    target = None
    for d in drafts:
        if d.get("draft_index") == draft_index and d.get("status") == "pending":
            target = d
            break
    if not target:
        return {"error": f"No pending draft at index {draft_index}"}
    state["drafts"] = [d for d in drafts if d.get("draft_index") != draft_index]
    _save_state(state)
    return {"discarded": draft_index, "job_title": target.get("job", {}).get("title", "?")}


def get_applier_status() -> dict:
    """Return summary of applier state."""
    state = _load_state()
    pending_drafts = [d for d in state.get("drafts", []) if d.get("status") == "pending"]
    return {
        "pending_drafts":   len(pending_drafts),
        "total_generated":  state.get("total_generated", 0),
        "total_sent":       state.get("total_sent", 0),
        "drafts":           pending_drafts,
    }


# ── Formatting ─────────────────────────────────────────────────────────────────

def format_apply_preview(result: dict) -> str:
    """Format a draft result as Telegram-safe HTML for review."""
    if "error" in result:
        return f"<b>Applier Error</b>\n<code>{result['error']}</code>"

    job       = result.get("job", {})
    title     = (job.get("title") or result.get("job_title", "?"))[:60]
    platform  = job.get("platform", "?")
    budget    = (
        f"${job.get('budget_min', 0)}–${job.get('budget_max', 0)}"
        if job.get("budget_max", 0) > 0
        else "Budget TBD"
    )
    draft_num = result.get("draft_index", "?")
    model     = result.get("model_used", "?")
    style     = result.get("style", "cold")

    final = result.get("draft_final", "").strip()
    raw   = result.get("draft_raw", "").strip()

    violations = result.get("violations", [])
    viol_block = ""
    if violations:
        viol_lines = "\n".join(f"  ⚠️ {v}" for v in violations[:3])
        viol_block = f"\n\n<b>Rule Violations:</b>\n{viol_lines}"

    lines = [
        f"<b>🦞 CashClaw Outreach Draft #{draft_num}</b>",
        f"<b>Gig:</b> {title}",
        f"<b>Platform:</b> {platform} · <b>Budget:</b> {budget}",
        f"<b>Style:</b> {style} · <b>Model:</b> {model}",
        "",
        "<b>── Final Message ──</b>",
        f"<i>{final}</i>",
        "",
        "<b>── Raw Ollama Draft ──</b>",
        f"<code>{raw[:300]}{'...' if len(raw) > 300 else ''}</code>",
        viol_block,
        "",
        f"✅ <code>/send_apply {draft_num}</code>  ·  ❌ <code>/discard_apply {draft_num}</code>",
    ]
    return "\n".join(l for l in lines if l is not None)


def format_applier_status(status: dict) -> str:
    """Format get_applier_status() for Telegram HTML."""
    lines = [
        "<b>🦞 CashClaw Applier Status</b>",
        "",
        f"Pending drafts:   <b>{status['pending_drafts']}</b>",
        f"Total generated:  {status['total_generated']}",
        f"Total sent:       {status['total_sent']}",
    ]
    drafts = status.get("drafts", [])
    if drafts:
        lines.append("\n<b>Pending Drafts:</b>")
        for d in drafts[:5]:
            idx   = d.get("draft_index", "?")
            title = (d.get("job", {}).get("title") or "?")[:40]
            lines.append(f"  #{idx} — {title}")
            lines.append(
                f"     /send_apply {idx}  ·  /discard_apply {idx}"
            )
    return "\n".join(lines)
