"""Content Pipeline Agent — Agent 3

Reformats clips to 9:16, generates viral captions via Claude Haiku,
and queues content for posting.

State file: data/content_queue.json
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.brain import ask_hybrid

_DATA_DIR = Path(__file__).parent.parent / "data"
_QUEUE_FILE = _DATA_DIR / "content_queue.json"

_EMPTY_QUEUE: dict = {
    "queue": [],
    "posted": [],
    "failed": [],
    "last_updated": None,
}


# ---------------------------------------------------------------------------
# Queue persistence
# ---------------------------------------------------------------------------

def _load_queue() -> dict:
    if _QUEUE_FILE.exists():
        try:
            return json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {k: list(v) if isinstance(v, list) else v for k, v in _EMPTY_QUEUE.items()}


def _save_queue(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _QUEUE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def reformat_to_9x16(clip_path: Path, output_dir: Path) -> Path:
    """Reformat a video clip to 9:16 (1080x1920) using FFmpeg.

    Returns the output path. Raises RuntimeError if FFmpeg fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = clip_path.stem
    out_path = output_dir / f"{stem}_9x16{clip_path.suffix}"

    cmd = [
        "ffmpeg",
        "-i", str(clip_path),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-y",
        str(out_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg exited {result.returncode}: {result.stderr[-500:]}"
            )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found — install FFmpeg and ensure it is on PATH"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg timed out after 300 seconds")

    return out_path


def generate_viral_captions(
    clip_path: Path,
    transcript: str = "",
    context: str = "",
) -> list[str]:
    """Generate 3 viral TikTok/Instagram captions using Claude Haiku.

    Returns a list of 3 caption strings.
    Falls back to placeholder captions on error.
    """
    prompt = (
        "Generate 3 viral TikTok/Instagram captions for this clip.\n"
        f"Context: {context}\n"
        f"Transcript snippet: {transcript[:200]}\n\n"
        "Each caption must have: a strong hook, 3-5 relevant hashtags, and a CTA.\n"
        "Format exactly like this:\n"
        "CAP1: <caption text>\n"
        "CAP2: <caption text>\n"
        "CAP3: <caption text>"
    )

    try:
        response, _ = ask_hybrid(prompt, force="complex")
        captions: list[str] = []
        for line in response.splitlines():
            line = line.strip()
            if line.startswith("CAP1:"):
                captions.append(line[5:].strip())
            elif line.startswith("CAP2:"):
                captions.append(line[5:].strip())
            elif line.startswith("CAP3:"):
                captions.append(line[5:].strip())
        # Pad if fewer than 3 were parsed
        while len(captions) < 3:
            captions.append(f"Caption {len(captions) + 1} — edit me before posting")
        return captions[:3]
    except Exception as exc:
        return [
            f"Caption 1 (AI error: {exc}) — edit before posting",
            "Caption 2 — add your hook + hashtags + CTA here",
            "Caption 3 — another option for A/B testing",
        ]


def add_to_queue(
    clip_path: str,
    captions: list[str],
    platform: str = "both",
    scheduled_at: Optional[str] = None,
) -> str:
    """Add a clip to the content queue. Returns the new item ID."""
    item_id = str(uuid.uuid4())[:8]
    item = {
        "id": item_id,
        "clip_path": clip_path,
        "title": Path(clip_path).stem,
        "captions": captions,
        "selected_caption": None,
        "platform": platform,
        "status": "queued",
        "scheduled_at": scheduled_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data = _load_queue()
    data["queue"].append(item)
    _save_queue(data)
    return item_id


def get_queue_status() -> dict:
    """Return counts for each status + next scheduled item."""
    data = _load_queue()
    all_items = data.get("queue", [])
    counts = {"queued": 0, "approved": 0, "posting": 0, "posted": 0, "failed": 0}
    next_scheduled = None

    for item in all_items:
        status = item.get("status", "queued")
        if status in counts:
            counts[status] += 1
        if (
            item.get("scheduled_at")
            and item.get("status") in ("queued", "approved")
            and (
                next_scheduled is None
                or item["scheduled_at"] < next_scheduled["scheduled_at"]
            )
        ):
            next_scheduled = item

    return {
        **counts,
        "total_queued": len(all_items),
        "total_posted": len(data.get("posted", [])),
        "total_failed": len(data.get("failed", [])),
        "next_scheduled": next_scheduled,
    }


def approve_queue_item(item_id: str, caption_index: int = 0) -> bool:
    """Approve a queue item and select its caption. Returns True if found."""
    data = _load_queue()
    for item in data["queue"]:
        if item["id"] == item_id:
            captions = item.get("captions", [])
            idx = max(0, min(caption_index, len(captions) - 1))
            item["status"] = "approved"
            item["selected_caption"] = captions[idx] if captions else ""
            _save_queue(data)
            return True
    return False


def remove_from_queue(item_id: str) -> bool:
    """Remove an item from the queue entirely. Returns True if found."""
    data = _load_queue()
    original_len = len(data["queue"])
    data["queue"] = [i for i in data["queue"] if i["id"] != item_id]
    if len(data["queue"]) < original_len:
        _save_queue(data)
        return True
    return False


def run_pipeline(
    clip_path: str,
    context: str = "",
    platform: str = "both",
    bot=None,
    chat_id: int = 0,
) -> dict:
    """Full pipeline: reformat -> generate captions -> queue -> notify.

    Returns {id, clip_path_9x16, captions, status}.
    """
    import asyncio

    clip = Path(clip_path)
    output_dir = _DATA_DIR / "clips_9x16"
    clip_9x16_path = clip_path  # fallback if reformat fails

    # Step 1: reformat
    try:
        out = reformat_to_9x16(clip, output_dir)
        clip_9x16_path = str(out)
    except Exception as exc:
        clip_9x16_path = clip_path
        reformat_note = f"⚠️ Reformat skipped: {exc}"
    else:
        reformat_note = f"✅ Reformatted → <code>{clip_9x16_path}</code>"

    # Step 2: generate captions
    captions = generate_viral_captions(
        clip,
        context=context,
    )

    # Step 3: add to queue
    item_id = add_to_queue(clip_9x16_path, captions, platform=platform)

    # Step 4: Telegram notification
    if bot and chat_id:
        cap_lines = "\n".join(
            f"  <b>{i + 1}.</b> {c}" for i, c in enumerate(captions)
        )
        msg = (
            f"🎬 <b>Content queued</b> — ID: <code>{item_id}</code>\n\n"
            f"{reformat_note}\n\n"
            f"<b>Caption options:</b>\n{cap_lines}\n\n"
            f"Approve with:\n"
            f"  /approve_content {item_id} 1\n"
            f"  /approve_content {item_id} 2\n"
            f"  /approve_content {item_id} 3"
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
        except Exception:
            pass

    return {
        "id": item_id,
        "clip_path_9x16": clip_9x16_path,
        "captions": captions,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_content_pipeline(
    clip_path: str,
    context: str = "",
    platform: str = "both",
    bot=None,
    chat_id: int = 0,
) -> dict:
    """Run full content pipeline for a clip. Returns result dict."""
    return run_pipeline(clip_path, context=context, platform=platform, bot=bot, chat_id=chat_id)


def get_content_queue() -> dict:
    """Return current queue status dict."""
    return get_queue_status()


def approve_content(item_id: str, caption_index: int = 0) -> str:
    """Approve a queue item by ID and caption index (0-based).

    Returns a confirmation string.
    """
    data = _load_queue()
    for item in data["queue"]:
        if item["id"] == item_id:
            ok = approve_queue_item(item_id, caption_index)
            if ok:
                captions = item.get("captions", [])
                idx = max(0, min(caption_index, len(captions) - 1))
                selected = captions[idx] if captions else "(no caption)"
                return (
                    f"✅ Item <code>{item_id}</code> approved.\n"
                    f"Selected caption {caption_index + 1}: {selected}"
                )
    return f"❌ Item <code>{item_id}</code> not found in queue."
