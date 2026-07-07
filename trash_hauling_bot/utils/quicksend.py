"""
Quick-send formatting helpers (Piece 3).

Pure, I/O-free functions that turn an *already approved* outreach message plus a
lead/queue item into:
  - a clean copy-paste block (the message, ready to paste anywhere), and
  - a one-tap deep link to launch the right messaging app.

This NEVER sends anything. It only formats text + builds a link so a human can
send manually with one tap (compliance: the bot must never auto-send/DM).

Channel selection:
  - If the lead has a usable phone number (>= 10 digits extracted from the
    ``contact`` field), build an ``sms:`` link with the message URL-encoded in
    the ``body`` parameter.
  - Otherwise fall back to a Facebook Messenger hint (``https://m.me/`` when a
    page/username is known) or a plain "reply in the Marketplace thread"
    instruction with the listing URL.
"""

import re
from typing import Dict
from urllib.parse import quote

# Phone digits embedded in a lead's ``contact`` field (already-normalized digit
# strings, or human-formatted numbers). Mirrors utils.sanitize._PHONE_RE so we
# stay consistent with how the scraper extracts numbers.
_PHONE_RE = re.compile(r"(\+?1?\s?[\(]?\d{3}[\)]?[\s.\-]?\d{3}[\s.\-]?\d{4})")

CHANNEL_SMS = "sms"
CHANNEL_MESSENGER = "messenger"
CHANNEL_MARKETPLACE = "marketplace"


def _extract_digits(contact: str) -> str:
    """Return a >= 10-digit phone string from ``contact``, or "" if none.

    Accepts either an already-normalized digit string (what the scraper stores)
    or a human-formatted number such as "(469) 618-7677".
    """
    if not isinstance(contact, str) or not contact.strip():
        return ""
    match = _PHONE_RE.search(contact)
    if match:
        digits = re.sub(r"[^\d]", "", match.group(1))
        return digits if len(digits) >= 10 else ""
    # Fall back: the contact may be a bare digit run with no separators that the
    # regex above did not anchor (e.g. a long string). Strip non-digits and use
    # it only if it looks like a phone number.
    digits = re.sub(r"[^\d]", "", contact)
    return digits if 10 <= len(digits) <= 15 else ""


def build_quicksend(message: str, lead: Dict) -> Dict:
    """Format an approved outreach ``message`` for fast manual send.

    Args:
        message: The approved outreach text (e.g. queue entry's ``message``).
        lead: A lead or pending-queue item. Read-only. Relevant keys:
            ``contact`` (phone digits / formatted number), ``listing_url``,
            and optionally ``messenger`` / ``m_me`` (a m.me username or url).

    Returns:
        ``{"copy_block": str, "send_link": str, "channel": str}``

        - ``copy_block``: the message, stripped, ready to copy-paste.
        - ``send_link``: a one-tap link (``sms:``/``https://m.me/``) or a short
          instruction when no deep link is possible.
        - ``channel``: one of ``"sms"``, ``"messenger"``, ``"marketplace"``.
    """
    message = (message or "").strip()
    lead = lead or {}

    copy_block = message

    digits = _extract_digits(lead.get("contact", ""))
    if digits:
        # sms:<digits>?body=<urlencoded message>. quote() leaves nothing unsafe
        # in the body so spaces become %20 and '&', '?' etc. are escaped, which
        # keeps the URI parseable across iOS/Android SMS handlers.
        body = quote(message, safe="")
        return {
            "copy_block": copy_block,
            "send_link": f"sms:{digits}?body={body}",
            "channel": CHANNEL_SMS,
        }

    # No phone -> Facebook Messenger / Marketplace fallback.
    handle = _messenger_handle(lead)
    if handle:
        return {
            "copy_block": copy_block,
            "send_link": f"https://m.me/{handle}",
            "channel": CHANNEL_MESSENGER,
        }

    listing_url = (lead.get("listing_url") or "").strip()
    if listing_url:
        hint = f"No phone on file - reply in the Marketplace thread: {listing_url}"
    else:
        hint = "No phone or thread on file - send via Messenger to the lead manually."
    return {
        "copy_block": copy_block,
        "send_link": hint,
        "channel": CHANNEL_MARKETPLACE,
    }


def _messenger_handle(lead: Dict) -> str:
    """Pull a m.me username/handle from a lead if one is present.

    Supports a raw username, a full ``m.me`` URL, or a facebook.com profile URL.
    Returns "" when nothing usable is found (the common case for scraped leads).
    """
    raw = (lead.get("m_me") or lead.get("messenger") or "").strip()
    if not raw:
        return ""
    raw = raw.rstrip("/")
    # Strip a leading m.me/ or facebook.com/ prefix to leave just the handle.
    raw = re.sub(r"^https?://(www\.)?(m\.me|facebook\.com)/", "", raw, flags=re.IGNORECASE)
    raw = raw.lstrip("@")
    return raw.split("?")[0].split("/")[0].strip()
