"""Social Publisher Agent — Agent 4

Auto-posts queued content to TikTok & Instagram on schedule.
Sends a daily Telegram preview gate before posting.

State file: data/publish_log.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).parent.parent / "data"
_PUBLISH_LOG = _DATA_DIR / "publish_log.json"
_QUEUE_FILE = _DATA_DIR / "content_queue.json"
_INCOME_LOG = _DATA_DIR / "income_log.json"

_EMPTY_LOG: dict = {
    "posts": [],
    "last_daily_preview_sent": None,
    "daily_preview_approved": False,
}


# ---------------------------------------------------------------------------
# Log persistence
# ---------------------------------------------------------------------------

def _load_log() -> dict:
    if _PUBLISH_LOG.exists():
        try:
            return json.loads(_PUBLISH_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _EMPTY_LOG.items()}


def _save_log(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PUBLISH_LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_queue() -> dict:
    if _QUEUE_FILE.exists():
        try:
            return json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"queue": [], "posted": [], "failed": []}


def _save_queue(data: dict) -> None:
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _QUEUE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Platform posting
# ---------------------------------------------------------------------------

def _post_tiktok(clip_path: str, caption: str) -> dict:
    """Post a video to TikTok via Content Posting API v2.

    Returns result dict with success, post_id, url, platform, and optional error.
    """
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        return {
            "success": False,
            "error": "TIKTOK_ACCESS_TOKEN not set in .env",
            "stub": True,
            "platform": "tiktok",
        }

    try:
        import requests

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = {
            "post_info": {
                "title": caption[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": Path(clip_path).stat().st_size if Path(clip_path).exists() else 0,
                "chunk_size": 10_000_000,
                "total_chunk_count": 1,
            },
        }
        resp = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        post_id = data.get("data", {}).get("publish_id", "")
        return {
            "success": True,
            "post_id": post_id,
            "url": f"https://www.tiktok.com/@me/video/{post_id}",
            "platform": "tiktok",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "platform": "tiktok",
        }


def _post_instagram(clip_path: str, caption: str) -> dict:
    """Post a video to Instagram via Graph API (reels upload).

    Returns result dict with success, post_id, platform, and optional error.
    """
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()

    if not token or not user_id:
        missing = []
        if not token:
            missing.append("INSTAGRAM_ACCESS_TOKEN")
        if not user_id:
            missing.append("INSTAGRAM_USER_ID")
        return {
            "success": False,
            "error": f"{', '.join(missing)} not set in .env",
            "stub": True,
            "platform": "instagram",
        }

    try:
        import requests

        # Step 1: create media container
        container_resp = requests.post(
            f"https://graph.facebook.com/v19.0/{user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": clip_path,  # must be a public URL for Graph API
                "caption": caption,
                "access_token": token,
            },
            timeout=60,
        )
        container_resp.raise_for_status()
        container_id = container_resp.json().get("id", "")

        if not container_id:
            raise ValueError("No container ID returned from Instagram")

        # Step 2: publish
        publish_resp = requests.post(
            f"https://graph.facebook.com/v19.0/{user_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        post_id = publish_resp.json().get("id", "")
        return {
            "success": True,
            "post_id": post_id,
            "platform": "instagram",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "platform": "instagram",
        }


# ---------------------------------------------------------------------------
# Daily preview gate
# ---------------------------------------------------------------------------

def send_daily_preview(bot, chat_id: int) -> bool:
    """Send the next 3 queued items to Telegram as a daily preview.

    Returns True if message was sent.
    """
    import asyncio

    data = _load_queue()
    queued = [i for i in data.get("queue", []) if i.get("status") in ("queued", "approved")][:3]

    if not queued:
        return False

    lines = []
    for i, item in enumerate(queued, 1):
        cap = item.get("selected_caption") or (item.get("captions") or ["(no caption)"])[0]
        lines.append(
            f"<b>{i}. {item.get('title', item['id'])}</b>\n"
            f"   Platform: {item.get('platform', 'both')} | Status: {item.get('status')}\n"
            f"   Caption: {cap[:80]}..."
        )

    msg = (
        "📅 <b>Daily Content Preview</b> — approve to post today\n\n"
        + "\n\n".join(lines)
        + "\n\n"
        "Commands:\n"
        "  /publish now    — post approved items now\n"
        "  /publish skip   — skip today's batch"
    )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(
                bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            )
        else:
            loop.run_until_complete(
                bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            )

        log = _load_log()
        log["last_daily_preview_sent"] = datetime.now(timezone.utc).isoformat()
        _save_log(log)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Publish cycle
# ---------------------------------------------------------------------------

def run_publish_cycle(bot=None, chat_id: int = 0) -> str:
    """Post all approved queue items to their platforms.

    Updates queue statuses and logs results.
    Returns a summary string.
    """
    queue_data = _load_queue()
    approved = [i for i in queue_data.get("queue", []) if i.get("status") == "approved"]

    if not approved:
        return "📭 No approved items ready to post."

    log = _load_log()
    results = []

    for item in approved:
        item_id = item["id"]
        clip = item.get("clip_path", "")
        caption = item.get("selected_caption") or ""
        platform = item.get("platform", "both")

        post_results = []

        if platform in ("tiktok", "both"):
            res = _post_tiktok(clip, caption)
            post_results.append(res)
            if res.get("success"):
                results.append(f"✅ TikTok: {item.get('title', item_id)}")
            else:
                results.append(
                    f"❌ TikTok ({item.get('title', item_id)}): {res.get('error', 'unknown')}"
                )

        if platform in ("instagram", "both"):
            res = _post_instagram(clip, caption)
            post_results.append(res)
            if res.get("success"):
                results.append(f"✅ Instagram: {item.get('title', item_id)}")
            else:
                results.append(
                    f"❌ Instagram ({item.get('title', item_id)}): {res.get('error', 'unknown')}"
                )

        all_success = all(r.get("success") for r in post_results)
        any_success = any(r.get("success") for r in post_results)

        # Update queue item status
        for q_item in queue_data["queue"]:
            if q_item["id"] == item_id:
                q_item["status"] = "posted" if any_success else "failed"
                q_item["posted_at"] = datetime.now(timezone.utc).isoformat()
                break

        # Move to posted/failed lists
        if any_success:
            item_copy = {**item, "status": "posted", "post_results": post_results}
            queue_data.setdefault("posted", []).append(item_copy)
        else:
            item_copy = {**item, "status": "failed", "post_results": post_results}
            queue_data.setdefault("failed", []).append(item_copy)

        # Log to publish_log
        log["posts"].append({
            "id": item_id,
            "title": item.get("title", item_id),
            "platform": platform,
            "results": post_results,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    # Remove posted/failed items from active queue
    posted_ids = {i["id"] for i in queue_data.get("posted", [])}
    failed_ids = {i["id"] for i in queue_data.get("failed", [])}
    done_ids = posted_ids | failed_ids
    queue_data["queue"] = [i for i in queue_data["queue"] if i["id"] not in done_ids]

    _save_queue(queue_data)
    _save_log(log)

    summary = f"📤 Published {len(approved)} item(s):\n" + "\n".join(results)

    if bot and chat_id:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(
                    bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")
                )
            else:
                loop.run_until_complete(
                    bot.send_message(chat_id=chat_id, text=summary, parse_mode="HTML")
                )
        except Exception:
            pass

    return summary


# ---------------------------------------------------------------------------
# Stats & income
# ---------------------------------------------------------------------------

def get_publish_stats() -> dict:
    """Return publishing statistics."""
    log = _load_log()
    posts = log.get("posts", [])

    tiktok_posts = sum(
        1 for p in posts
        for r in p.get("results", [])
        if r.get("platform") == "tiktok" and r.get("success")
    )
    instagram_posts = sum(
        1 for p in posts
        for r in p.get("results", [])
        if r.get("platform") == "instagram" and r.get("success")
    )

    last_post = posts[-1]["ts"] if posts else None

    queue_data = _load_queue()
    pending_count = len(
        [i for i in queue_data.get("queue", []) if i.get("status") in ("queued", "approved")]
    )

    return {
        "total_posted": len(posts),
        "tiktok_posts": tiktok_posts,
        "instagram_posts": instagram_posts,
        "last_post": last_post,
        "pending_count": pending_count,
        "last_daily_preview_sent": log.get("last_daily_preview_sent"),
        "daily_preview_approved": log.get("daily_preview_approved", False),
    }


def log_income(amount: float, source: str, note: str = "") -> None:
    """Append an income entry to data/income_log.json."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    entries: list = []
    if _INCOME_LOG.exists():
        try:
            entries = json.loads(_INCOME_LOG.read_text(encoding="utf-8"))
        except Exception:
            entries = []

    entries.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "amount": float(amount),
        "source": source,
        "note": note,
    })
    _INCOME_LOG.write_text(json.dumps(entries, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_social_publisher(bot=None, chat_id: int = 0) -> str:
    """Run the publish cycle and return a summary string."""
    return run_publish_cycle(bot=bot, chat_id=chat_id)


def send_preview(bot, chat_id: int) -> bool:
    """Send daily content preview to Telegram. Returns True if sent."""
    return send_daily_preview(bot, chat_id)
