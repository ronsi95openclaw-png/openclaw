"""
Compliance guardrails (cross-cutting).

These helpers enforce the HaulY'all hard rules (see COMPLIANCE.md):
  * AI only DRAFTS. A human must approve before anything is sent/posted.
  * No automated posting/DMing on Facebook; no auto-send to customers.
  * Paid ads go through the official Meta Ads API only (never browser automation).
  * The scraper stays read-only with human-like pacing (jitter).

Import and call these at any boundary that could externally send or post, so the
no-auto-send rule is enforced in code rather than only documented.
"""

import logging
import random
import time
from typing import Set

logger = logging.getLogger(__name__)

# Actions the bot is allowed to perform itself (internal / draft-only / read-only).
ALLOWED_ACTIONS: Set[str] = {
    "scrape_read",          # read-only Marketplace browsing (paced)
    "draft_outreach",       # generate an outreach message for human review
    "draft_ad",             # generate paste-ready ad campaign text
    "draft_image_prompt",   # build a brand-image prompt for the Hermes/Higgsfield layer
    "notify_team",          # internal Telegram message to the team
    "mark_sent",            # mark a draft "sent" AFTER a human /confirm (no network send)
}

# Actions the bot must NEVER perform automatically. These require a human to act
# manually (or an official API gated on approval); the bot only drafts.
FORBIDDEN_ACTIONS: Set[str] = {
    "fb_post",          # auto-post to Marketplace / FB groups
    "fb_dm",            # auto-DM / cold-message on Facebook
    "send_sms",         # auto-text a customer
    "send_email",       # auto-email a customer
    "send_messenger",   # auto-send via Messenger
    "ads_post",         # browser-automate Meta Ads creation/posting
}


class ComplianceError(RuntimeError):
    """Raised when a compliance guard is violated (e.g. an unapproved send)."""


def assert_human_approved(flag: bool, action: str) -> None:
    """Guard an externally-facing action behind explicit human approval.

    Call this immediately before any code path that could send or post. ``flag``
    must be the result of a real human approval (e.g. Ronnie running ``/confirm``),
    never a default ``True``. Raises ``ComplianceError`` if not approved.
    """
    if not flag:
        logger.error("Compliance: blocked unapproved action %r (no human approval)", action)
        raise ComplianceError(
            f"Action '{action}' requires explicit human approval and was not approved. "
            "AI only drafts; a human must approve before anything is sent or posted."
        )
    logger.info("Compliance: human-approved action %r", action)


def is_outbound_allowed(action: str) -> bool:
    """Return True only for actions the bot may perform itself.

    Forbidden outbound actions (auto post/DM/send, browser-automated ads) return
    False. Unknown actions also return False (fail closed). This documents the
    allowed/forbidden split in one place; see ``ALLOWED_ACTIONS`` /
    ``FORBIDDEN_ACTIONS``.
    """
    if action in FORBIDDEN_ACTIONS:
        return False
    return action in ALLOWED_ACTIONS


def human_pace_sleep(min_s: float = 2.0, max_s: float = 4.0) -> float:
    """Sleep a jittered, human-like interval (blocking wrapper around time.sleep).

    Used to keep read-only scraping paced so it never looks like a bot burst.
    Returns the actual seconds slept. ``min_s``/``max_s`` may be passed in either
    order; values are clamped to be non-negative.
    """
    lo = max(0.0, min(min_s, max_s))
    hi = max(0.0, max(min_s, max_s))
    delay = random.uniform(lo, hi)
    time.sleep(delay)
    return delay
