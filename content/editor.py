"""FFmpeg-based video editor for OpenClaw content pipeline.

Handles:
- Auto-cut to target duration
- Resize / crop to 9:16 vertical (1080x1920)
- Whisper transcription + burned-in captions
- Background music mixing
- OpenClaw branding overlay (top-left watermark text)

Requires ffmpeg on PATH and openai-whisper installed.
"""
from __future__ import annotations

import os
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import whisper

WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_DURATION = 30  # seconds — clips longer than this are trimmed


def _get_random_music(music_folder: str | None = None) -> Optional[Path]:
    folder = Path(music_folder or os.getenv("MUSIC_FOLDER", "content/music"))
    if not folder.exists():
        return None
    tracks = [f for f in folder.iterdir() if f.suffix.lower() in {".mp3", ".wav"}]
    return random.choice(tracks) if tracks else None


def _transcribe(video_path: Path) -> list[dict]:
    """Run Whisper on the video and return word-level segments."""
    model = whisper.load_model(WHISPER_MODEL_NAME)
    result = model.transcribe(str(video_path), word_timestamps=True)
    return result.get("segments", [])


def _build_subtitle_filter(segments: list[dict]) -> str:
    """Build an ffmpeg drawtext filter chain from Whisper segments."""
    filters = []
    for seg in segments:
        text = seg.get("text", "").strip().replace("'", "\\'").replace(":", "\\:")
        if not text:
            continue
        start = seg["start"]
        end = seg["end"]
        filters.append(
            f"drawtext=text='{text}'"
            f":fontsize=52:fontcolor=white:borderw=3:bordercolor=black"
            f":x=(w-text_w)/2:y=h*0.80"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )
    return ",".join(filters) if filters else "null"


def process_video(
    input_path: Path,
    output_path: Path,
    music_folder: str | None = None,
    add_captions: bool = True,
) -> Path:
    """Edit a raw video into a social-media-ready 9:16 reel.

    Steps:
    1. Trim to TARGET_DURATION
    2. Crop & scale to 1080x1920 (9:16)
    3. Transcribe with Whisper and burn captions
    4. Mix in background music at low volume
    5. Add OpenClaw branding watermark

    Args:
        input_path: Source video file.
        output_path: Where to write the finished reel.
        music_folder: Override music folder path.
        add_captions: Set False to skip Whisper transcription.

    Returns:
        output_path on success.

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails.
        FileNotFoundError: If input_path does not exist.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Step 1 & 2: Trim + crop to 9:16 ---
    crop_filter = (
        f"trim=duration={TARGET_DURATION},"
        f"setpts=PTS-STARTPTS,"
        f"crop=ih*9/16:ih,"
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}"
    )

    # --- Step 3: Captions ---
    caption_filter = "null"
    if add_captions:
        print(f"  🎙  Transcribing with Whisper ({WHISPER_MODEL_NAME})...")
        segments = _transcribe(input_path)
        caption_filter = _build_subtitle_filter(segments)

    # --- Step 4: Branding watermark ---
    # Emoji in FFmpeg drawtext requires emoji-capable fonts; use ASCII only
    branding_filter = (
        "drawtext=text='OpenClaw'"
        ":fontsize=38:fontcolor=white:borderw=2:bordercolor=black"
        ":x=20:y=20"
    )

    video_filter = f"{crop_filter},{caption_filter},{branding_filter}"

    music_path = _get_random_music(music_folder)

    if music_path:
        # Mix music at -18dB under original audio
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-i", str(music_path),
            "-filter_complex",
            f"[0:v]{video_filter}[v];"
            f"[0:a]volume=1.0[orig];"
            f"[1:a]volume=0.15,atrim=duration={TARGET_DURATION},asetpts=PTS-STARTPTS[music];"
            f"[orig][music]amix=inputs=2:duration=first[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", video_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]

    print(f"  🎬 Running FFmpeg...")
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    print(f"  ✅ Edited reel saved: {output_path}")
    return output_path
