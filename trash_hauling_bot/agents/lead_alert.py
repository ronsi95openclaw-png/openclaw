"""
New-lead alert formatting.

Builds a concise Telegram message summarizing the new leads found in a scrape
run (title, location, urgency/size score, link). Pure and dependency-free so
it stays trivial to test and reuse — the actual send happens via the Telegram
bot's notify_team() in the orchestrator.
"""

from typing import Dict, List

_MAX_LEADS_IN_ALERT = 10
_TITLE_MAX = 80


def _lead_title(lead: Dict) -> str:
    """Best-effort short title from a lead's description / job type."""
    title = (lead.get("description") or "").strip().replace("\n", " ")
    if not title:
        title = (lead.get("job_type") or "Lead").strip()
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1].rstrip() + "…"
    return title


def format_new_leads_alert(leads: List[Dict]) -> str:
    """Format a Telegram-ready summary of new leads.

    Returns an empty string when there are no leads, so callers can skip the
    send cleanly.
    """
    if not leads:
        return ""

    count = len(leads)
    header = f"*HaulYeah — {count} new lead" + ("s*" if count != 1 else "*")
    lines = [header]

    for lead in leads[:_MAX_LEADS_IN_ALERT]:
        title = _lead_title(lead)
        location = (lead.get("location") or "N/A").strip() or "N/A"
        urgency = lead.get("urgency_score", "?")
        size = lead.get("size_score", "?")
        url = (lead.get("listing_url") or "").strip()
        line = (
            f"\n• {title}\n"
            f"  {location} | urgency {urgency}/10 | size {size}/10"
        )
        if url:
            line += f"\n  {url}"
        lines.append(line)

    remaining = count - _MAX_LEADS_IN_ALERT
    if remaining > 0:
        lines.append(f"\n…and {remaining} more.")

    return "\n".join(lines)
