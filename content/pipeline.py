"""Full content pipeline orchestrator for OpenClaw.

Flow:
    New video in GDrive folder
        → editor.py  (trim, 9:16, captions, music, branding)
        → caption_generator.py  (LLM Instagram + TikTok copy)
        → uploader.py  (send to Telegram for /approve or /reject)

Telegram status updates are sent at each stage so you can monitor
progress from your phone.

Usage:
    py -m content.pipeline          # start watching (blocking)
    py -m content.pipeline --once path/to/video.mp4   # process one file
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from content.caption_generator import generate_captions
from content.editor import process_video, _transcribe
from content.uploader import send_for_approval_sync, send_status_sync
from content.watcher import start_watching


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def process(video_path: Path) -> None:
    """Run the full pipeline for a single video file.

    Args:
        video_path: Absolute path to the raw input video.
    """
    name = video_path.stem
    print(f"\n{'='*50}")
    print(f"🎬 Pipeline started: {name}")
    print(f"{'='*50}")

    send_status_sync(
        f"🎬 <b>Pipeline started</b>\n"
        f"📁 File: <code>{video_path.name}</code>\n"
        f"⏰ {_timestamp()}"
    )

    # --- Stage 1: Edit ---
    output_dir = video_path.parent / "output"
    output_path = output_dir / f"{name}_reel.mp4"

    send_status_sync("⚙️ <b>Stage 1/3</b> — Editing video (trim, 9:16, captions, music)...")
    print("\n[1/3] Editing video...")

    try:
        process_video(video_path, output_path)
    except Exception as exc:
        msg = f"🚨 <b>Edit failed</b>\n{video_path.name}\n<code>{exc}</code>"
        send_status_sync(msg)
        print(f"  ❌ Edit failed: {exc}")
        return

    send_status_sync("✅ <b>Stage 1/3 done</b> — Video edited.")

    # --- Stage 2: Generate captions ---
    send_status_sync("🧠 <b>Stage 2/3</b> — Generating captions with LLM...")
    print("\n[2/3] Generating captions...")

    try:
        segments = _transcribe(video_path)
        transcript = " ".join(seg.get("text", "") for seg in segments).strip()
        if not transcript:
            transcript = "Crypto trading bot live signal reel"
        captions = generate_captions(transcript)
    except Exception as exc:
        msg = f"🚨 <b>Caption generation failed</b>\n<code>{exc}</code>"
        send_status_sync(msg)
        print(f"  ❌ Caption generation failed: {exc}")
        return

    send_status_sync("✅ <b>Stage 2/3 done</b> — Captions generated.")

    # --- Stage 3: Send for approval ---
    send_status_sync("📤 <b>Stage 3/3</b> — Sending reel to Telegram for approval...")
    print("\n[3/3] Sending for approval...")

    try:
        send_for_approval_sync(output_path, captions)
    except Exception as exc:
        msg = f"🚨 <b>Upload failed</b>\n<code>{exc}</code>"
        send_status_sync(msg)
        print(f"  ❌ Upload failed: {exc}")
        return

    print(f"\n✅ Pipeline complete: {output_path.name}")
    print("   Reply /approve or /reject in Telegram.")


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw content pipeline")
    parser.add_argument(
        "--once",
        metavar="VIDEO",
        help="Process a single video file then exit",
    )
    args = parser.parse_args()

    if args.once:
        process(Path(args.once).resolve())
    else:
        start_watching(callback=process, blocking=True)


if __name__ == "__main__":
    main()
