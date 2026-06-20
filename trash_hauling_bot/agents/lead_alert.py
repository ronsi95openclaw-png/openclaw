"""
Lead-alert digest — a compact, hard-length-bounded summary of new leads.

Why this exists: the scheduled "haulyeah-lead-alert" job was failing with
`RuntimeError: Response remained truncated after 3 continuation attempts` —
i.e. the alert payload grew unbounded as leads accumulated and overflowed the
message limit. `build_digest` guarantees a short, predictable message: it
ranks leads by urgency, keeps only the top few, caps the total character
count, and rolls everything else into a "+N more" footer.

Pure and dependency-free so it is trivial to unit-test and to call from the
Telegram bot or any external cron.
"""

from typing import Dict, List, Optional

# Telegram hard-caps a message at 4096 chars; stay well under so the alert
# never needs continuation (which is what was truncating the cron response).
DEFAULT_MAX_CHARS = 1200
DEFAULT_MAX_LEADS = 8


def _lead_line(lead: Dict) -> str:
    """One compact line per lead: id, urgency, job type, location."""
    lead_id = str(lead.get("id", "?"))[:12]
    urgency = lead.get("urgency_score", "?")
    job = str(lead.get("job_type", "lead")).strip()[:28] or "lead"
    loc = str(lead.get("location", "")).strip()[:24]
    loc_part = f" — {loc}" if loc else ""
    return f"• {lead_id} [u{urgency}] {job}{loc_part}"


def build_digest(
    leads: Optional[List[Dict]],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_leads: int = DEFAULT_MAX_LEADS,
) -> str:
    """Build a compact, length-bounded new-leads alert.

    - Ranks by urgency_score (desc) so the most time-sensitive leads show first.
    - Shows at most `max_leads` lines and never exceeds `max_chars`.
    - Anything not shown is summarized as a "+N more" footer.

    The returned string is guaranteed to be <= max_chars.
    """
    leads = leads or []
    total = len(leads)
    if total == 0:
        return "🗑️ HaulYeah leads: no new leads this cycle."

    def _urgency(lead: Dict) -> int:
        try:
            return int(lead.get("urgency_score", 0))
        except (TypeError, ValueError):
            return 0

    ranked = sorted(leads, key=_urgency, reverse=True)

    header = f"🗑️ HaulYeah — {total} new lead{'s' if total != 1 else ''}"
    lines: List[str] = [header]
    shown = 0

    for lead in ranked[:max_leads]:
        line = _lead_line(lead)
        remaining = total - shown
        # Reserve room for a possible footer so we never blow past max_chars.
        footer_reserve = len(f"\n…+{remaining} more — /leads for the full list")
        projected = len("\n".join(lines)) + 1 + len(line) + footer_reserve
        if projected > max_chars:
            break
        lines.append(line)
        shown += 1

    if shown < total:
        lines.append(f"…+{total - shown} more — /leads for the full list")

    digest = "\n".join(lines)
    # Final hard guarantee, even against pathological inputs.
    if len(digest) > max_chars:
        digest = digest[: max_chars - 1].rstrip() + "…"
    return digest
