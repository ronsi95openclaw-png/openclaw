"""LLM-powered caption and hashtag generator for OpenClaw reels.

Uses brain.py (Ollama) to write platform-optimised captions for Instagram
and TikTok based on the video transcript and optional context.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.brain import ask_llm


@dataclass
class Captions:
    instagram: str
    tiktok: str
    hashtags: str


_INSTAGRAM_PROMPT = """\
You are a social media copywriter for a crypto trading bot called ClawBot / OpenClaw.
Write an Instagram caption for a short trading reel.

Transcript: {transcript}
Context: {context}

Requirements:
- Hook in the first line (no hashtags yet)
- 3-5 sentences max, conversational and confident
- End with a CTA like "Follow for daily signals" or "Drop a 🦾 if you're watching"
- Then 20-25 relevant hashtags on a new line
- Tone: sharp, crypto-native, not cringe

Reply with ONLY the caption text and hashtags, nothing else."""

_TIKTOK_PROMPT = """\
You are a social media copywriter for a crypto trading bot called ClawBot / OpenClaw.
Write a TikTok caption for a short trading reel.

Transcript: {transcript}
Context: {context}

Requirements:
- Max 150 characters for the caption itself (TikTok limit)
- Punchy, hype, emoji-forward
- Then 5-8 trending hashtags on a new line (shorter list than Instagram)
- Tone: fast, energetic, gen-Z friendly but still credible

Reply with ONLY the caption text and hashtags, nothing else."""


def generate_captions(
    transcript: str,
    context: str = "Crypto trading reel showing ClawBot live signals",
) -> Captions:
    """Generate Instagram and TikTok captions using the local LLM.

    Args:
        transcript: Whisper-generated transcript of the reel audio.
        context: Optional extra context to steer the copy.

    Returns:
        Captions dataclass with instagram, tiktok, and hashtags fields.
    """
    print("  🧠 Generating Instagram caption...")
    instagram_raw = ask_llm(
        _INSTAGRAM_PROMPT.format(transcript=transcript, context=context)
    )

    print("  🧠 Generating TikTok caption...")
    tiktok_raw = ask_llm(
        _TIKTOK_PROMPT.format(transcript=transcript, context=context)
    )

    # Extract shared hashtags from the Instagram response (last line block)
    lines = instagram_raw.strip().splitlines()
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    hashtags = " ".join(hashtag_lines)
    instagram_body = "\n".join(l for l in lines if not l.strip().startswith("#")).strip()

    return Captions(
        instagram=f"{instagram_body}\n\n{hashtags}",
        tiktok=tiktok_raw.strip(),
        hashtags=hashtags,
    )
