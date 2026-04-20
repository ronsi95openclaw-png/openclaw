"""
Clip Processor — Downloads a VOD via yt-dlp, splits into clips with FFmpeg,
transcribes each clip with Whisper, and generates viral TikTok captions via LLM.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.clip_processor")

_DATA_DIR        = Path(__file__).parent.parent / "data"
_JOBS_FILE       = _DATA_DIR / "clip_jobs.json"
_CLIPS_BASE_DIR  = _DATA_DIR / "clips"

_DEFAULT_JOBS: dict = {"jobs": [], "completed": [], "failed": []}


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_jobs() -> dict:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _JOBS_FILE.exists():
        try:
            return json.loads(_JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_DEFAULT_JOBS)


def _save_jobs(jobs: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _JOBS_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


# ── Tool availability ──────────────────────────────────────────────────────────

def _is_tool_available(name: str) -> bool:
    """Return True if the tool (yt-dlp / ffmpeg) is on PATH."""
    return shutil.which(name) is not None


def is_whisper_available() -> bool:
    """Return True if the voice.voice_handler reports Whisper installed."""
    try:
        from voice.voice_handler import is_whisper_available as _wh
        return _wh()
    except Exception:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False


# ── Core pipeline functions ────────────────────────────────────────────────────

def download_vod(url: str, output_dir: Path) -> Path:
    """Download a VOD using yt-dlp.

    Args:
        url:        Video URL (YouTube, Twitch VOD, etc.)
        output_dir: Directory to save the download.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If yt-dlp is not installed or download fails.
    """
    if not _is_tool_available("yt-dlp"):
        raise RuntimeError(
            "yt-dlp is not installed. Install with: pip install yt-dlp"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "best",
        "-o", output_template,
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out after 300 seconds.")
    except Exception as exc:
        raise RuntimeError(f"yt-dlp subprocess failed: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed (code {result.returncode}): {result.stderr[:300]}"
        )

    # Find the downloaded file (yt-dlp renames it to the actual title)
    files = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    video_extensions = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".ts"}
    for f in files:
        if f.suffix.lower() in video_extensions:
            return f

    raise RuntimeError("yt-dlp completed but no video file was found in output_dir.")


def extract_clips(
    video_path: Path,
    clip_duration: int = 60,
    output_dir: Optional[Path] = None,
) -> list[Path]:
    """Split a video into N-second clips using FFmpeg segment muxer.

    Args:
        video_path:    Path to the source video file.
        clip_duration: Duration of each clip in seconds.
        output_dir:    Directory for output clips (default: alongside video).

    Returns:
        List of Paths to the generated clip files.

    Raises:
        RuntimeError: If FFmpeg is not installed or segmentation fails.
    """
    if not _is_tool_available("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is not installed. Install with: winget install ffmpeg"
        )

    if output_dir is None:
        output_dir = video_path.parent / "clips"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_pattern = str(output_dir / "clip_%03d.mp4")

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-c", "copy",
        "-map", "0",
        "-segment_time", str(clip_duration),
        "-f", "segment",
        "-reset_timestamps", "1",
        output_pattern,
        "-y",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out after 600 seconds.")
    except Exception as exc:
        raise RuntimeError(f"ffmpeg subprocess failed: {exc}") from exc

    if result.returncode != 0:
        # ffmpeg writes progress to stderr — non-zero may still produce files
        logger.warning("ffmpeg returned code %s: %s", result.returncode, result.stderr[:200])

    clips = sorted(output_dir.glob("clip_*.mp4"))
    if not clips:
        raise RuntimeError("ffmpeg ran but produced no clip files.")
    return clips


def transcribe_clip(clip_path: Path) -> str:
    """Transcribe a clip using Whisper if available, else return empty string."""
    if not is_whisper_available():
        logger.info("Whisper not available — skipping transcription for %s", clip_path.name)
        return ""

    try:
        import whisper  # type: ignore
        model = whisper.load_model("base")
        result = model.transcribe(str(clip_path))
        return result.get("text", "").strip()
    except Exception as exc:
        logger.warning("Whisper transcription failed for %s: %s", clip_path.name, exc)
        return ""


def generate_caption(clip_path: Path, transcript: str) -> str:
    """Generate 3 viral TikTok captions via LLM.

    Args:
        clip_path:  Path to the clip (used for filename context).
        transcript: Whisper transcript text (may be empty).

    Returns:
        Formatted string with 3 numbered captions.
    """
    context = transcript[:300] if transcript else f"[clip: {clip_path.name}]"
    prompt = (
        f"Write 3 viral TikTok captions for a clip. "
        f"Transcript: {context}. "
        f"Return ONLY the 3 captions numbered 1-3."
    )
    try:
        from core.brain import ask_hybrid
        response, _ = ask_hybrid(prompt, force="complex")
        return response.strip()
    except Exception as exc:
        logger.warning("generate_caption LLM call failed: %s", exc)
        return "1. Check this out!\n2. You won't believe this clip\n3. Watch till the end!"


# ── Full pipeline ──────────────────────────────────────────────────────────────

def _send_progress(bot, chat_id: int, text: str) -> None:
    """Fire-and-forget Telegram progress message."""
    if not (bot and chat_id):
        return
    import asyncio
    try:
        asyncio.get_event_loop().run_until_complete(
            bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        )
    except RuntimeError:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            )
            loop.close()
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)


def process_vod(
    url: str,
    clip_duration: int = 60,
    chat_id: int = 0,
    bot=None,
) -> dict:
    """Full clip processing pipeline.

    Steps: download → extract clips → transcribe each → generate captions.

    Args:
        url:           VOD URL to process.
        clip_duration: Target duration of each clip in seconds.
        chat_id:       Telegram chat ID for progress updates.
        bot:           Telegram Bot instance.

    Returns:
        Dict with job_id, clips_processed, output_dir, status.
    """
    job_id     = str(uuid.uuid4())[:8]
    output_dir = _CLIPS_BASE_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs_data = _load_jobs()
    job_entry = {
        "job_id":       job_id,
        "url":          url,
        "clip_duration": clip_duration,
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "status":       "running",
        "clips":        [],
        "output_dir":   str(output_dir),
    }
    jobs_data["jobs"].append(job_entry)
    _save_jobs(jobs_data)

    _send_progress(bot, chat_id, f"<b>Clip Job {job_id}</b> started.\nDownloading VOD...")

    try:
        # 1. Download
        video_path = download_vod(url, output_dir)
        _send_progress(
            bot, chat_id,
            f"<b>Job {job_id}</b> — Downloaded: <code>{video_path.name}</code>\nSplitting into {clip_duration}s clips..."
        )

        # 2. Extract clips
        clips_dir = output_dir / "clips"
        clip_paths = extract_clips(video_path, clip_duration=clip_duration, output_dir=clips_dir)
        _send_progress(
            bot, chat_id,
            f"<b>Job {job_id}</b> — {len(clip_paths)} clips created.\nTranscribing + generating captions..."
        )

        # 3. Transcribe + caption each clip
        clip_results = []
        for i, clip_path in enumerate(clip_paths, 1):
            transcript = transcribe_clip(clip_path)
            captions   = generate_caption(clip_path, transcript)
            clip_results.append({
                "clip":       clip_path.name,
                "path":       str(clip_path),
                "transcript": transcript[:500] if transcript else "",
                "captions":   captions,
            })
            logger.info("Processed clip %d/%d: %s", i, len(clip_paths), clip_path.name)

        # 4. Update state
        jobs_data = _load_jobs()
        for entry in jobs_data["jobs"]:
            if entry["job_id"] == job_id:
                entry["status"]          = "complete"
                entry["clips"]           = clip_results
                entry["clips_processed"] = len(clip_results)
                entry["completed_at"]    = datetime.now(timezone.utc).isoformat()
                jobs_data["completed"].append(entry)
                jobs_data["jobs"].remove(entry)
                break
        _save_jobs(jobs_data)

        result = {
            "job_id":          job_id,
            "clips_processed": len(clip_results),
            "output_dir":      str(output_dir),
            "status":          "complete",
        }

        _send_progress(
            bot, chat_id,
            f"<b>Job {job_id} Complete!</b>\n"
            f"Clips: {len(clip_results)}\n"
            f"Output: <code>{output_dir}</code>"
        )
        return result

    except Exception as exc:
        logger.error("process_vod failed for job %s: %s", job_id, exc, exc_info=True)

        jobs_data = _load_jobs()
        for entry in jobs_data["jobs"]:
            if entry["job_id"] == job_id:
                entry["status"] = "failed"
                entry["error"]  = str(exc)
                jobs_data["failed"].append(entry)
                jobs_data["jobs"].remove(entry)
                break
        _save_jobs(jobs_data)

        _send_progress(
            bot, chat_id,
            f"<b>Job {job_id} Failed</b>\nError: <code>{str(exc)[:200]}</code>"
        )
        return {
            "job_id":    job_id,
            "status":    "failed",
            "error":     str(exc),
            "output_dir": str(output_dir),
        }


def get_clip_status(job_id: str) -> dict:
    """Return the job entry for a given job_id, or an error dict."""
    data = _load_jobs()
    for entry in data.get("jobs", []) + data.get("completed", []) + data.get("failed", []):
        if entry.get("job_id") == job_id:
            return entry
    return {"error": f"Job {job_id} not found"}


# ── Public API ─────────────────────────────────────────────────────────────────

def process_vod_url(
    url: str,
    clip_duration: int = 60,
    bot=None,
    chat_id: int = 0,
) -> dict:
    """Public entry point: run full VOD processing pipeline."""
    return process_vod(url=url, clip_duration=clip_duration, chat_id=chat_id, bot=bot)


def get_clip_jobs() -> list:
    """Return all jobs (active + completed + failed) as a flat list."""
    data = _load_jobs()
    all_jobs = (
        data.get("jobs", [])
        + data.get("completed", [])
        + data.get("failed", [])
    )
    # Sort newest first
    all_jobs.sort(
        key=lambda j: j.get("started_at", ""),
        reverse=True,
    )
    return all_jobs


def calculate_projections() -> dict:
    """Return base income projection estimates for the clip economy dashboard.

    When no real income has been logged yet this returns motivational base
    estimates so the /clip-economy page shows meaningful numbers instead of $0.
    Once real income data is available this function should be extended to
    compute projections from actual earnings history.
    """
    return {
        "actual_earned":        0.0,
        "tiktok_fund_est":      0.0,
        "conservative_monthly": 200.0,   # 2 gigs/mo × $100 avg
        "current_monthly":      260.0,   # conservative × 1.3
        "optimized_monthly":    500.0,   # full pipeline running
        "is_estimate":          True,
    }


def format_clip_jobs_summary(jobs: list) -> str:
    """Format get_clip_jobs() list as Telegram HTML."""
    if not jobs:
        return "<b>Clip Jobs</b>\n\nNo jobs yet.\nUse: <code>/clip [url] [duration]</code>"

    lines = [f"<b>Clip Jobs ({len(jobs)})</b>\n"]
    for job in jobs[:10]:
        jid    = job.get("job_id", "?")
        status = job.get("status", "?")
        clips  = job.get("clips_processed", len(job.get("clips", [])))
        url    = job.get("url", "")[:50]
        ts     = job.get("started_at", "")[:16].replace("T", " ")

        status_icon = {"complete": "✅", "running": "⚡", "failed": "❌"}.get(status, "❓")
        lines.append(
            f"{status_icon} <code>{jid}</code> — {clips} clips\n"
            f"   {url}\n"
            f"   {ts} UTC"
        )
    return "\n\n".join(lines)
