"""Agent 5: Performance Tracker

Pulls social stats every 6h, builds performance DB with income estimates.
Tracks TikTok + Instagram metrics and projects monthly earnings.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

_PERF_DB_FILE  = DATA_DIR / "performance_db.json"
_INCOME_LOG    = DATA_DIR / "income_log.json"

# TikTok Creator Fund average: $0.03 per 1,000 views
TIKTOK_CPM = 0.03 / 1000  # per view


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_perf_db() -> dict:
    default = {"snapshots": [], "income_log": [], "weekly_summary": {}, "last_updated": None}
    try:
        if _PERF_DB_FILE.exists():
            return json.loads(_PERF_DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_perf_db(db: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PERF_DB_FILE.write_text(json.dumps(db, indent=2), encoding="utf-8")


# ── Social platform fetchers ───────────────────────────────────────────────────

def fetch_tiktok_stats(post_id: Optional[str] = None) -> dict:
    """Fetch TikTok video stats via Open API or return stub with zeros."""
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    stub = {
        "post_id": post_id or "unknown",
        "platform": "tiktok",
        "views": 0,
        "likes": 0,
        "shares": 0,
        "comments": 0,
        "estimated_earnings_usd": 0.0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stub": True,
    }
    if not token:
        return stub

    try:
        import requests
        url = "https://open.tiktokapis.com/v2/video/query/"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        params = {"fields": "view_count,like_count,share_count,comment_count"}
        body = {}
        if post_id:
            body["filters"] = {"video_ids": [post_id]}

        resp = requests.post(url, headers=headers, json=body, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            videos = data.get("data", {}).get("videos", [{}])
            v = videos[0] if videos else {}
            views = int(v.get("view_count", 0))
            return {
                "post_id": post_id or v.get("id", "unknown"),
                "platform": "tiktok",
                "views": views,
                "likes": int(v.get("like_count", 0)),
                "shares": int(v.get("share_count", 0)),
                "comments": int(v.get("comment_count", 0)),
                "estimated_earnings_usd": round(views * TIKTOK_CPM, 4),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "stub": False,
            }
    except Exception as exc:
        stub["error"] = str(exc)[:120]

    return stub


def fetch_instagram_stats(media_id: Optional[str] = None) -> dict:
    """Fetch Instagram media insights via Graph API or return stub."""
    token   = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
    stub = {
        "media_id": media_id or "unknown",
        "platform": "instagram",
        "reach": 0,
        "impressions": 0,
        "likes": 0,
        "saves": 0,
        "estimated_earnings_usd": 0.0,  # Instagram doesn't pay directly
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stub": True,
    }
    if not token or not user_id:
        return stub
    if not media_id:
        return stub

    try:
        import requests
        url    = f"https://graph.facebook.com/v19.0/{media_id}/insights"
        params = {
            "metric": "reach,impressions,likes,saved",
            "access_token": token,
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            metrics = {m["name"]: m.get("values", [{}])[0].get("value", 0)
                       for m in data.get("data", [])}
            return {
                "media_id": media_id,
                "platform": "instagram",
                "reach":       int(metrics.get("reach", 0)),
                "impressions": int(metrics.get("impressions", 0)),
                "likes":       int(metrics.get("likes", 0)),
                "saves":       int(metrics.get("saved", 0)),
                "estimated_earnings_usd": 0.0,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "stub": False,
            }
    except Exception as exc:
        stub["error"] = str(exc)[:120]

    return stub


# ── Income log ─────────────────────────────────────────────────────────────────

def load_income_log() -> list:
    """Read data/income_log.json — created by social_publisher.log_income()."""
    try:
        if _INCOME_LOG.exists():
            return json.loads(_INCOME_LOG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


# ── Projections ────────────────────────────────────────────────────────────────

def calculate_income_projections(days: int = 30) -> dict:
    """Calculate income projections from income log and performance snapshots."""
    income_log = load_income_log()
    db         = _load_perf_db()

    now      = datetime.now(timezone.utc)
    cutoff   = now - timedelta(days=days)
    cutoff_s = cutoff.isoformat()

    # Sum actual income in last N days
    actual_earned = sum(
        float(e.get("amount", 0))
        for e in income_log
        if e.get("ts", "") >= cutoff_s
    )

    # Sum TikTok fund estimates from snapshots
    tiktok_fund_est = sum(
        float(s.get("estimated_earnings_usd", 0))
        for snap in db.get("snapshots", [])
        if snap.get("ts", "") >= cutoff_s
        for s in snap.get("posts", [])
        if s.get("platform") == "tiktok"
    )

    # Monthly projections
    if days < 30:
        conservative_monthly = actual_earned * (30 / days) if days > 0 else 0.0
    else:
        conservative_monthly = actual_earned

    current_monthly   = round(conservative_monthly * 1.3, 2)
    optimized_monthly = round(conservative_monthly * 2.5, 2)

    return {
        "actual_earned":        round(actual_earned, 2),
        "tiktok_fund_est":      round(tiktok_fund_est, 4),
        "conservative_monthly": round(conservative_monthly, 2),
        "current_monthly":      current_monthly,
        "optimized_monthly":    optimized_monthly,
        "days_analyzed":        days,
    }


# ── Snapshot ───────────────────────────────────────────────────────────────────

def take_snapshot() -> dict:
    """Fetch stats for recent posts and append to performance_db.json."""
    db   = _load_perf_db()
    now  = datetime.now(timezone.utc).isoformat()

    # Gather post IDs from recent income log entries
    income_log = load_income_log()
    posts_data = []

    seen_tiktok = set()
    seen_ig     = set()

    for entry in income_log[-20:]:
        pid = entry.get("post_id") or entry.get("id")
        src = entry.get("source", "").lower()
        if "tiktok" in src and pid and pid not in seen_tiktok:
            seen_tiktok.add(pid)
            posts_data.append(fetch_tiktok_stats(pid))
        elif "instagram" in src and pid and pid not in seen_ig:
            seen_ig.add(pid)
            posts_data.append(fetch_instagram_stats(pid))

    # If no posts found, take stub snapshot with zeros
    if not posts_data:
        posts_data = [
            fetch_tiktok_stats(),
            fetch_instagram_stats(),
        ]

    snapshot = {
        "ts":    now,
        "posts": posts_data,
        "total_tiktok_views":    sum(p.get("views", 0)       for p in posts_data if p.get("platform") == "tiktok"),
        "total_tiktok_earnings": sum(p.get("estimated_earnings_usd", 0) for p in posts_data if p.get("platform") == "tiktok"),
        "total_ig_reach":        sum(p.get("reach", 0)        for p in posts_data if p.get("platform") == "instagram"),
        "post_count":            len(posts_data),
    }

    db.setdefault("snapshots", []).append(snapshot)
    # Keep last 1000 snapshots
    if len(db["snapshots"]) > 1000:
        db["snapshots"] = db["snapshots"][-1000:]
    db["last_updated"] = now

    _save_perf_db(db)
    return snapshot


# ── Tracker cycle ──────────────────────────────────────────────────────────────

def run_tracker_cycle(bot=None, chat_id: int = 0) -> str:
    """Take snapshot; send Telegram summary every 6h if bot+chat_id provided."""
    import time

    db       = _load_perf_db()
    now_ts   = time.time()
    last_upd = db.get("last_updated")

    # Determine whether 6h have passed since last snapshot
    should_notify = False
    if last_upd:
        try:
            last_dt  = datetime.fromisoformat(last_upd)
            elapsed  = (datetime.now(timezone.utc) - last_dt).total_seconds()
            should_notify = elapsed >= 6 * 3600
        except Exception:
            should_notify = True
    else:
        should_notify = True

    snapshot    = take_snapshot()
    projections = calculate_income_projections(days=7)

    status = (
        f"Snapshot taken: {snapshot['post_count']} posts tracked. "
        f"TikTok views: {snapshot['total_tiktok_views']:,}. "
        f"Est. 7-day earnings: ${projections['actual_earned']:.2f} actual + "
        f"${projections['tiktok_fund_est']:.4f} TikTok fund."
    )

    if bot and chat_id and should_notify:
        try:
            import asyncio
            msg = (
                "📊 *Performance Tracker Update*\n\n"
                f"TikTok views: {snapshot['total_tiktok_views']:,}\n"
                f"TikTok est. earnings: ${snapshot['total_tiktok_earnings']:.4f}\n"
                f"IG reach: {snapshot['total_ig_reach']:,}\n\n"
                f"💰 Monthly projection (conservative): ${projections['conservative_monthly']:.2f}\n"
                f"📈 Current pace: ${projections['current_monthly']:.2f}\n"
                f"🚀 Optimized: ${projections['optimized_monthly']:.2f}"
            )
            asyncio.get_event_loop().run_until_complete(
                bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            )
        except Exception:
            pass

    return status


# ── Summary ────────────────────────────────────────────────────────────────────

def get_performance_summary(days: int = 7) -> dict:
    """Return total_views, total_posts, avg_views_per_post, income_projections, top_performing_post."""
    db     = _load_perf_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    recent_snaps = [s for s in db.get("snapshots", []) if s.get("ts", "") >= cutoff]

    all_posts: list[dict] = []
    for snap in recent_snaps:
        all_posts.extend(snap.get("posts", []))

    total_views = sum(p.get("views", 0) for p in all_posts if p.get("platform") == "tiktok")
    total_posts = len(all_posts)
    avg_views   = round(total_views / total_posts, 1) if total_posts > 0 else 0.0

    top_post = max(all_posts, key=lambda p: p.get("views", 0), default=None)

    return {
        "total_views":        total_views,
        "total_posts":        total_posts,
        "avg_views_per_post": avg_views,
        "income_projections": calculate_income_projections(days=days),
        "top_performing_post": top_post,
        "days":               days,
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def run_performance_tracker(bot=None, chat_id: int = 0) -> str:
    """Public entry point — run tracker cycle."""
    return run_tracker_cycle(bot=bot, chat_id=chat_id)


def calculate_projections() -> dict:
    """Public entry point — return income projections for last 30 days."""
    return calculate_income_projections(days=30)
