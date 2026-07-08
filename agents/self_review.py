"""Agent 6: Self Review

Runs Sunday midnight — Claude Haiku analyzes 30 days of data,
auto-applies low-risk config fixes, flags big changes for human review.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

_CONFIG_FILE   = DATA_DIR / "config" / "clip_economy.json"
_REVIEW_LOG    = DATA_DIR / "self_review_history.json"
_PERF_DB_FILE  = DATA_DIR / "performance_db.json"
_INCOME_LOG    = DATA_DIR / "income_log.json"
_PUBLISH_LOG   = DATA_DIR / "publish_log.json"
_JOBS_LOG      = DATA_DIR / "job_scout_log.json"


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    default = {
        "job_scout": {
            "scan_interval_hours": 6,
            "min_score": 6,
            "max_budget_usd": 500,
            "search_keywords": ["clip editor", "video editor", "short form", "reels", "TikTok"],
        },
        "clip_processor": {
            "default_clip_duration": 60,
            "max_clips_per_vod": 10,
            "whisper_model": "base",
        },
        "content_pipeline": {
            "default_platform": "both",
            "caption_style": "viral",
            "post_times": ["09:00", "17:00", "20:00"],
        },
        "social_publisher": {
            "daily_limit_tiktok": 3,
            "daily_limit_instagram": 3,
            "require_preview_approval": True,
        },
        "performance": {
            "track_interval_hours": 6,
            "income_target_monthly": 1000,
        },
    }
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_config(cfg: dict) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ── Review history ─────────────────────────────────────────────────────────────

def _load_review_history() -> list:
    try:
        if _REVIEW_LOG.exists():
            return json.loads(_REVIEW_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _append_review_history(entry: dict) -> None:
    history = _load_review_history()
    history.append(entry)
    # Keep last 52 reviews (1 year)
    if len(history) > 52:
        history = history[-52:]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _REVIEW_LOG.write_text(json.dumps(history, indent=2), encoding="utf-8")


# ── Data aggregation ───────────────────────────────────────────────────────────

def load_30day_report() -> dict:
    """Aggregate last 30 days of income, performance, publish, and job data."""
    cutoff_s = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # Income log
    try:
        income_log = json.loads(_INCOME_LOG.read_text(encoding="utf-8")) if _INCOME_LOG.exists() else []
    except Exception:
        income_log = []
    recent_income = [e for e in income_log if e.get("ts", "") >= cutoff_s]
    total_earned  = sum(float(e.get("amount", 0)) for e in recent_income)

    # Performance DB
    try:
        perf_db = json.loads(_PERF_DB_FILE.read_text(encoding="utf-8")) if _PERF_DB_FILE.exists() else {}
    except Exception:
        perf_db = {}
    recent_snaps = [s for s in perf_db.get("snapshots", []) if s.get("ts", "") >= cutoff_s]
    all_views    = sum(s.get("total_tiktok_views", 0) for s in recent_snaps)
    avg_views    = round(all_views / len(recent_snaps), 1) if recent_snaps else 0.0

    # Publish log
    try:
        publish_log = json.loads(_PUBLISH_LOG.read_text(encoding="utf-8")) if _PUBLISH_LOG.exists() else []
    except Exception:
        publish_log = []
    recent_posts    = [p for p in publish_log if p.get("ts", "") >= cutoff_s]
    posts_published = len(recent_posts)

    # Determine top content type
    platform_counts: dict = {}
    for p in recent_posts:
        plat = p.get("platform", "unknown")
        platform_counts[plat] = platform_counts.get(plat, 0) + 1
    top_content_type = max(platform_counts, key=platform_counts.get) if platform_counts else "none"

    # Job scout
    try:
        jobs_log = json.loads(_JOBS_LOG.read_text(encoding="utf-8")) if _JOBS_LOG.exists() else []
    except Exception:
        jobs_log = []
    recent_jobs    = [j for j in jobs_log if j.get("ts", "") >= cutoff_s]
    jobs_completed = len([j for j in recent_jobs if j.get("status") == "applied"])

    # Weakest area heuristic
    weakest_area = "unknown"
    if total_earned == 0:
        weakest_area = "monetization — no income recorded"
    elif posts_published == 0:
        weakest_area = "content publishing — no posts"
    elif jobs_completed == 0:
        weakest_area = "job applications — none submitted"

    return {
        "total_earned":     round(total_earned, 2),
        "posts_published":  posts_published,
        "avg_views":        avg_views,
        "jobs_completed":   jobs_completed,
        "top_content_type": top_content_type,
        "weakest_area":     weakest_area,
        "income_entries":   len(recent_income),
        "snapshots_count":  len(recent_snaps),
    }


# ── LLM analysis ──────────────────────────────────────────────────────────────

def analyze_with_llm(report: dict) -> dict:
    """Call ask_hybrid (force=complex) to get config improvement suggestions."""
    import sys
    sys.path.insert(0, str(ROOT))
    from core.brain import ask_hybrid

    system_prompt = "You are a growth analyst for a content creator monetization system."
    user_prompt   = (
        "Analyze 30 days of performance data and suggest config improvements:\n"
        + json.dumps(report, indent=2)
        + "\n\nReturn JSON with:\n"
        "- low_risk_changes: list of {param, old_value, new_value, reason} — safe to auto-apply\n"
        "- high_risk_changes: list of {param, reason} — needs human review\n"
        "- summary: 2-sentence performance assessment"
    )

    try:
        response, _brain = ask_hybrid(user_prompt, system=system_prompt, force="complex")
        # Extract JSON from response
        raw = response.strip()
        # Strip markdown code fences if present
        if "```" in raw:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            raw   = raw[start:end] if start >= 0 and end > start else raw
        parsed = json.loads(raw)
        return {
            "low_risk_changes":  parsed.get("low_risk_changes", []),
            "high_risk_changes": parsed.get("high_risk_changes", []),
            "summary":           parsed.get("summary", "No summary provided."),
        }
    except json.JSONDecodeError:
        # Return the raw summary text if JSON parse fails
        return {
            "low_risk_changes":  [],
            "high_risk_changes": [],
            "summary":           response[:500] if response else "LLM analysis failed.",
        }
    except Exception as exc:
        return {
            "low_risk_changes":  [],
            "high_risk_changes": [],
            "summary":           f"Analysis error: {exc}",
        }


# ── Apply changes ──────────────────────────────────────────────────────────────

def apply_low_risk_changes(changes: list) -> list:
    """Apply low-risk config changes to data/config/clip_economy.json only."""
    if not changes:
        return []

    cfg     = _load_config()
    applied = []

    for change in changes:
        param     = change.get("param", "")
        new_value = change.get("new_value")
        reason    = change.get("reason", "")

        # Safety: only allow dot-path navigation within the config dict
        # e.g. "clip_processor.default_clip_duration"
        if not param:
            continue

        parts = param.split(".")
        obj   = cfg

        try:
            for p in parts[:-1]:
                if not isinstance(obj, dict) or p not in obj:
                    raise KeyError(f"Key '{p}' not found in config")
                obj = obj[p]

            leaf = parts[-1]
            if not isinstance(obj, dict) or leaf not in obj:
                raise KeyError(f"Leaf key '{leaf}' not found in config")

            old_value = obj[leaf]
            obj[leaf] = new_value

            applied.append({
                "param":     param,
                "old_value": old_value,
                "new_value": new_value,
                "reason":    reason,
                "ts":        datetime.now(timezone.utc).isoformat(),
            })
        except (KeyError, TypeError):
            # Skip changes to non-existent paths
            continue

    if applied:
        _save_config(cfg)

    return applied


# ── Main review ────────────────────────────────────────────────────────────────

def run_self_review(bot=None, chat_id: int = 0) -> str:
    """Run full self-review cycle and optionally notify via Telegram."""
    report          = load_30day_report()
    analysis        = analyze_with_llm(report)
    applied_changes = apply_low_risk_changes(analysis.get("low_risk_changes", []))

    now_s = datetime.now(timezone.utc).isoformat()

    # Persist review history
    _append_review_history({
        "ts":             now_s,
        "report":         report,
        "analysis":       analysis,
        "applied_changes": applied_changes,
    })

    summary       = analysis.get("summary", "No summary.")
    high_risk     = analysis.get("high_risk_changes", [])
    n_applied     = len(applied_changes)

    # Build Telegram message
    changes_lines = "\n".join(
        f"- {c['param']}: {c['old_value']} → {c['new_value']} ({c['reason']})"
        for c in applied_changes
    ) or "None"

    high_risk_lines = "\n".join(
        f"- {h.get('param', 'unknown')}: {h.get('reason', '')}"
        for h in high_risk
    ) or "None"

    tg_msg = (
        "🔄 *Weekly Self-Review Complete*\n\n"
        f"📊 *30-Day Summary:* {summary}\n\n"
        f"✅ *Auto-applied {n_applied} changes:*\n{changes_lines}\n\n"
        f"⚠️ *Flagged for your review:*\n{high_risk_lines}"
    )

    if bot and chat_id:
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                bot.send_message(chat_id=chat_id, text=tg_msg, parse_mode="Markdown")
            )
        except Exception:
            pass

    return (
        f"Self-review complete. Applied {n_applied} low-risk changes. "
        f"{len(high_risk)} items flagged for human review. "
        f"Summary: {summary[:100]}"
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def get_review_history() -> list:
    """Return list of past self-review records."""
    return _load_review_history()
