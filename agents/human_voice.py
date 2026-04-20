"""HumanVoice — Two-pass AI humanizer for CashClaw outreach.

Pass 1:  Ollama (qwen2.5:14b) generates a raw outreach draft from the gig context.
Pass 2:  Claude Haiku rewrites it to sound like Ronnie — direct, no-BS, builder energy.

Hard rules baked into the Haiku rewrite prompt:
  - No "I hope this finds you well" or any opener like that
  - No corporate/agency language ("leverage", "synergy", "deliverables", "bespoke")
  - Never start a sentence with "I"
  - Max 4 sentences for cold outreach, 6 for follow-up
  - Must reference one specific detail from the actual listing
  - No em-dashes — use plain dashes or just rewrite

Public API:
    generate_outreach(job: dict, style: str = "cold") -> dict
      Returns: {draft_raw, draft_final, model_used, tokens_used}

    humanize(text: str, context: str = "") -> str
      Pass a custom raw block through Haiku humanization only.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("openclaw.human_voice")

# ── Haiku rewrite system prompt ───────────────────────────────────────────────

_HAIKU_SYSTEM = """\
You are a writing editor who makes AI-drafted outreach messages sound like they were \
written by a real person named Ronnie — a freelance developer and builder who ships fast, \
talks straight, and doesn't try too hard.

Your job: take the RAW DRAFT and rewrite it to sound authentically human.

HARD RULES — break any of these and the output is rejected:
1. Never start any sentence with "I" — restructure the sentence instead
2. No openers like "I hope this finds you well", "Hope you're doing great", "Reaching out because", "My name is"
3. No corporate/agency words: leverage, synergy, deliverables, bespoke, utilize, solutions, expertise
4. No em-dashes (—). Use a plain dash (-) or rewrite.
5. Cold outreach: max 4 sentences total. Follow-up: max 6 sentences.
6. Must include one specific detail pulled from the actual listing (title, niche, platform, budget range) — not generic filler
7. End with a clear, low-friction call to action (a question or an offer, never "let me know your thoughts")
8. Sound like a builder, not an agency. Casual confidence, no desperation.

Output ONLY the final rewritten message — no labels, no explanations, no quotes around it.\
"""

_OLLAMA_DRAFT_PROMPT_TEMPLATE = """\
Write a short cold outreach message for this freelance gig.
Be direct. Mention what you can do for them. Don't pad it out.

GIG TITLE: {title}
PLATFORM: {platform}
BUDGET: {budget}
DESCRIPTION: {description}
STYLE: {style}

Write the raw first draft now:\
"""

_HAIKU_REWRITE_PROMPT_TEMPLATE = """\
RAW DRAFT:
{raw_draft}

GIG CONTEXT (use one specific detail in your rewrite):
Title: {title}
Platform: {platform}
Budget: {budget}
Description snippet: {description}
Style: {style}

Rewrite this so it sounds like Ronnie wrote it — follow all the hard rules.\
"""

# ── Internal helpers ──────────────────────────────────────────────────────────

def _format_budget(job: dict) -> str:
    bmin = job.get("budget_min", 0)
    bmax = job.get("budget_max", 0)
    if bmax > 0:
        return f"${bmin}–${bmax}"
    if bmin > 0:
        return f"${bmin}+"
    return "unspecified"


def _ollama_draft(prompt: str, model: str = None) -> str:
    """Generate a raw draft via local Ollama."""
    import os
    model = model or os.getenv("OLLAMA_MODEL", "gemma3")
    try:
        from ollama import chat as ollama_chat
        response = ollama_chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response["message"]["content"].strip()
    except Exception as exc:
        logger.warning("Ollama draft failed: %s", exc)
        return ""


def _haiku_rewrite(system: str, user_prompt: str) -> tuple[str, int]:
    """Rewrite via Claude Haiku. Returns (text, input_tokens)."""
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set — skipping Haiku rewrite")
            return "", 0

        client = anthropic.Anthropic(api_key=api_key)
        model  = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

        msg = client.messages.create(
            model=model,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text   = msg.content[0].text.strip() if msg.content else ""
        tokens = msg.usage.input_tokens if msg.usage else 0
        return text, tokens
    except Exception as exc:
        logger.warning("Haiku rewrite failed: %s", exc)
        return "", 0


def _validate_output(text: str) -> list[str]:
    """Return list of rule violations found in the final output."""
    violations = []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    for s in sentences:
        if re.match(r"^I\b", s):
            violations.append(f'Sentence starts with "I": {s[:60]}')

    bad_openers = [
        "i hope this finds you", "hope you're", "reaching out because",
        "my name is", "i am writing", "i wanted to reach",
    ]
    text_lower = text.lower()
    for opener in bad_openers:
        if opener in text_lower:
            violations.append(f'Bad opener found: "{opener}"')

    bad_words = [
        "leverage", "synergy", "deliverables", "bespoke",
        "utilize", "solutions", "expertise",
    ]
    for word in bad_words:
        if re.search(r"\b" + word + r"\b", text_lower):
            violations.append(f'Corporate word found: "{word}"')

    if "—" in text:
        violations.append("Em-dash (—) found — replace with plain dash or rewrite")

    return violations


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_outreach(job: dict, style: str = "cold") -> dict:
    """Full two-pass pipeline: Ollama draft → Haiku humanize.

    Args:
        job:   Job dict from job_scout (needs title, description, budget_min,
               budget_max, platform, url)
        style: "cold" (default) or "follow_up"

    Returns:
        {
          "draft_raw":    str,   # Ollama first pass
          "draft_final":  str,   # Haiku humanized
          "violations":   list,  # rule check on final
          "model_used":   str,
          "tokens_used":  int,
          "job_title":    str,
        }
    """
    title       = job.get("title", "Untitled Gig")[:80]
    platform    = job.get("platform", "whop")
    budget      = _format_budget(job)
    description = (job.get("description", "") or "")[:250]

    # Pass 1: Ollama raw draft
    ollama_prompt = _OLLAMA_DRAFT_PROMPT_TEMPLATE.format(
        title=title,
        platform=platform,
        budget=budget,
        description=description,
        style=style,
    )
    raw_draft = _ollama_draft(ollama_prompt)

    if not raw_draft:
        # Fallback: a minimal stub so pass 2 still runs
        raw_draft = (
            f"Hey, saw your listing for {title}. "
            f"Can help with that — I build bots, automation, and content tools. "
            f"Available now. Want to connect?"
        )

    # Pass 2: Haiku humanization
    haiku_prompt = _HAIKU_REWRITE_PROMPT_TEMPLATE.format(
        raw_draft=raw_draft,
        title=title,
        platform=platform,
        budget=budget,
        description=description[:150],
        style=style,
    )
    final_text, tokens = _haiku_rewrite(_HAIKU_SYSTEM, haiku_prompt)

    if not final_text:
        # If Haiku is unavailable, use the Ollama draft with basic cleanup
        final_text = raw_draft
        model_used = "ollama-only"
        tokens     = 0
    else:
        model_used = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

    violations = _validate_output(final_text)
    if violations:
        logger.info(
            "HumanVoice violations (%d) in outreach for '%s': %s",
            len(violations), title, violations,
        )

    return {
        "draft_raw":   raw_draft,
        "draft_final": final_text,
        "violations":  violations,
        "model_used":  model_used,
        "tokens_used": tokens,
        "job_title":   title,
    }


def humanize(text: str, context: str = "") -> str:
    """Pass any raw text block through Haiku humanization only.

    Useful for rewriting follow-up messages, email replies, DMs, etc.

    Args:
        text:    Raw text to humanize
        context: Optional context string (gig title, client name, etc.)

    Returns:
        Humanized string (falls back to original if Haiku unavailable)
    """
    user_prompt = f"RAW DRAFT:\n{text}"
    if context:
        user_prompt += f"\n\nCONTEXT:\n{context}"
    user_prompt += "\n\nRewrite this so it sounds like Ronnie wrote it — follow all the hard rules."

    result, _ = _haiku_rewrite(_HAIKU_SYSTEM, user_prompt)
    return result if result else text
