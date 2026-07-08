#!/usr/bin/env python3
"""
TJR YouTube Strategy Extractor
Pulls TJR funded account videos -> extracts rules -> feeds to Vibe-Trading
"""
import os
import json
from datetime import datetime
from pathlib import Path

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    import yt_dlp
    DEPS_AVAILABLE = True
except ImportError:
    DEPS_AVAILABLE = False

CHANNEL_URL = "https://www.youtube.com/@TJRTrades"
OUTPUT_DIR = Path("sub-agents/trading-agent/tjr_transcripts")
STRATEGY_OUTPUT = Path("sub-agents/trading-agent/TJR_RULEBOOK.md")

FUNDED_KEYWORDS = [
    "funded", "prop firm", "eval", "evaluation",
    "pass", "profit target", "drawdown", "consistency",
    "lucid", "apex", "topstep", "rules"
]

def get_channel_videos(channel_url: str, limit: int = None) -> list[dict]:
    """Fetch video IDs and titles from TJR channel"""
    ydl_opts = {'extract_flat': True, 'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        videos = info.get('entries', [])
        result = [{'id': v['id'], 'title': v['title']} for v in videos if v.get('id')]
        return result[:limit] if limit else result

def is_funded_video(title: str) -> bool:
    return any(kw in title.lower() for kw in FUNDED_KEYWORDS)

def get_transcript(video_id: str) -> str | None:
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join([t['text'] for t in transcript])
    except Exception as e:
        print(f"  No transcript for {video_id}: {e}")
        return None

def extract_all(limit: int = None):
    if not DEPS_AVAILABLE:
        print("Missing deps: pip install youtube-transcript-api yt-dlp")
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching TJR channel videos...")
    all_videos = get_channel_videos(CHANNEL_URL, limit)
    print(f"   Found {len(all_videos)} videos")

    funded_videos = [v for v in all_videos if is_funded_video(v['title'])]
    print(f"   {len(funded_videos)} funded account videos")

    transcripts = []
    for v in funded_videos:
        print(f"  {v['title'][:60]}...")
        text = get_transcript(v['id'])
        if text:
            transcripts.append({
                'title': v['title'],
                'video_id': v['id'],
                'url': f"https://youtube.com/watch?v={v['id']}",
                'transcript': text
            })

    master_path = OUTPUT_DIR / "_ALL_TRANSCRIPTS.json"
    with open(master_path, 'w', encoding='utf-8') as f:
        json.dump(transcripts, f, indent=2)

    print(f"\nSaved {len(transcripts)} transcripts to {OUTPUT_DIR}")
    return str(master_path)

if __name__ == "__main__":
    import sys
    limit = int(sys.argv[2]) if "--limit" in sys.argv else None
    path = extract_all(limit=limit)
    if path:
        print(f"\nReady for analysis: {path}")
