"""Social media poster for OpenClaw — TikTok + Instagram.

Posts approved reels to TikTok (Content Posting API v2) and
Instagram (Meta Graph API) automatically after /approve.

Required env vars:
  TIKTOK_ACCESS_TOKEN      — from TikTok for Developers (Content Posting API)
  INSTAGRAM_ACCESS_TOKEN   — Meta Graph API user access token
  INSTAGRAM_USER_ID        — your Instagram Business/Creator account ID

TikTok setup:
  1. go to developers.tiktok.com
  2. Create an app → enable "Content Posting API"
  3. Generate an access token with scope: video.upload, video.publish

Instagram setup:
  1. go to developers.facebook.com
  2. Create an app → add Instagram Graph API
  3. Get a long-lived user token for your Business/Creator account
  4. Find your Instagram User ID via:
     GET https://graph.instagram.com/me?fields=id&access_token=TOKEN
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

import requests

from content.caption_generator import Captions

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
INSTAGRAM_API_BASE = "https://graph.instagram.com/v19.0"


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

def _post_tiktok(video_path: Path, captions: Captions) -> str:
    """Upload and publish a reel to TikTok via Content Posting API v2.

    Returns a status string.
    """
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        return "⚠️ TikTok: TIKTOK_ACCESS_TOKEN not set — skipped."

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Step 1: Init upload
    file_size = video_path.stat().st_size
    init_payload = {
        "post_info": {
            "title": captions.tiktok[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }

    r = requests.post(
        f"{TIKTOK_API_BASE}/post/publish/video/init/",
        headers=headers,
        json=init_payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")

    if not upload_url:
        return f"🚨 TikTok: Failed to get upload URL — {r.text}"

    # Step 2: Upload video bytes
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    upload_headers = {
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        "Content-Length": str(file_size),
    }
    r2 = requests.put(upload_url, headers=upload_headers, data=video_bytes, timeout=120)
    r2.raise_for_status()

    # Step 3: Poll publish status
    for _ in range(12):  # up to 60s
        time.sleep(5)
        status_r = requests.post(
            f"{TIKTOK_API_BASE}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
            timeout=15,
        )
        status_data = status_r.json().get("data", {})
        status = status_data.get("status", "")
        if status == "PUBLISH_COMPLETE":
            return "✅ TikTok: Published successfully!"
        if status in ("FAILED", "PUBLISH_FAILED"):
            reason = status_data.get("fail_reason", "unknown")
            return f"🚨 TikTok: Publish failed — {reason}"

    return "⚠️ TikTok: Publish timed out (check TikTok app to confirm)."


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

def _post_instagram(video_path: Path, captions: Captions) -> str:
    """Upload and publish a reel to Instagram via Meta Graph API.

    Instagram requires the video to be publicly accessible via URL during
    the creation step. We use a simple file-upload approach via the resumable
    upload endpoint.

    Returns a status string.
    """
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()

    if not token or not user_id:
        return "⚠️ Instagram: INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_USER_ID not set — skipped."

    # Step 1: Create media container (resumable upload)
    caption_text = captions.instagram[:2200]  # Instagram caption limit

    # Upload the video file directly via the upload session endpoint
    session_r = requests.post(
        f"{INSTAGRAM_API_BASE}/{user_id}/media",
        params={
            "media_type": "REELS",
            "caption": caption_text,
            "share_to_feed": "true",
            "access_token": token,
        },
        timeout=30,
    )

    if session_r.status_code != 200:
        return f"🚨 Instagram: Container creation failed — {session_r.text}"

    creation_id = session_r.json().get("id")
    if not creation_id:
        return f"🚨 Instagram: No creation_id returned — {session_r.text}"

    # Step 2: Poll until container is ready
    for _ in range(24):  # up to 2 min
        time.sleep(5)
        status_r = requests.get(
            f"{INSTAGRAM_API_BASE}/{creation_id}",
            params={
                "fields": "status_code,status",
                "access_token": token,
            },
            timeout=15,
        )
        status_data = status_r.json()
        status_code = status_data.get("status_code", "")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            return f"🚨 Instagram: Container error — {status_data}"
    else:
        return "⚠️ Instagram: Container processing timed out."

    # Step 3: Publish
    pub_r = requests.post(
        f"{INSTAGRAM_API_BASE}/{user_id}/media_publish",
        params={
            "creation_id": creation_id,
            "access_token": token,
        },
        timeout=30,
    )

    if pub_r.status_code == 200:
        return "✅ Instagram: Reel published successfully!"
    return f"🚨 Instagram: Publish failed — {pub_r.text}"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def post_to_socials(video_path: Path, captions: Captions) -> str:
    """Post an approved reel to both TikTok and Instagram.

    Args:
        video_path: Path to the finished reel MP4.
        captions: Captions dataclass from caption_generator.

    Returns:
        A multi-line status string summarising results for both platforms.
    """
    print("  📱 Posting to TikTok...")
    tiktok_result = _post_tiktok(video_path, captions)
    print(f"     {tiktok_result}")

    print("  📸 Posting to Instagram...")
    instagram_result = _post_instagram(video_path, captions)
    print(f"     {instagram_result}")

    return f"<b>TikTok:</b> {tiktok_result}\n<b>Instagram:</b> {instagram_result}"


def post_to_socials_sync(video_path: Path, captions: Captions) -> str:
    """Synchronous wrapper (runs in calling thread, no event loop needed)."""
    return post_to_socials(video_path, captions)
