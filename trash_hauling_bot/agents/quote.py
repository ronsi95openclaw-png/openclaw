"""
Quote estimator — maps a free-text job description to a flat-rate price tier.

Pure, dependency-free pricing logic so it is trivial to unit test and reuse
from outreach messages, the Telegram bot, or future booking flows.
"""

from typing import Optional

PRICING = {
    "minimum": 89,
    "quarter": 199,
    "half": 349,
    "three_quarter": 499,
    "full": 699,
}

# Checked largest-first: a description that hints at a big load should win over
# an incidental small-item cue ("a few items, well actually a full truck worth").
_TIER_KEYWORDS = [
    ("full", ["full truck", "whole house", "everything", "lots", "ton of"]),
    ("three_quarter", ["most of", "large amount", "big pile"]),
    ("half", ["half", "medium", "several items", "couch and"]),
    ("quarter", ["few items", "small", "couple", "chair", "mattress"]),
]


def estimate_tier(description: Optional[str]) -> str:
    """Classify a job description into a pricing tier; defaults to 'minimum'."""
    desc = (description or "").lower()
    for tier, keywords in _TIER_KEYWORDS:
        if any(kw in desc for kw in keywords):
            return tier
    return "minimum"


def estimate(description: Optional[str]) -> dict:
    """Return tier, price, and a customer-facing price range for a description."""
    tier = estimate_tier(description)
    price = PRICING[tier]
    high = min(price + 100, PRICING["full"])
    return {"tier": tier, "price": price, "high": high, "range": f"${price}-${high}"}


def format_quote(description: Optional[str], business_name: str = "HaulYA'LL!") -> str:
    """Build a short, friendly customer message with the estimated price range."""
    est = estimate(description)
    return (
        f"Thanks for reaching out to {business_name} — happy to help! "
        f"Based on what you described, your estimate is {est['range']}. "
        f"That covers loading, hauling, and responsible disposal — "
        f"final price is confirmed on-site. "
        f"Reply with your preferred date and address to get on the schedule."
    )
