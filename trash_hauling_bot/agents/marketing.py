"""
Marketing content — DFW outreach scripts, Meta ad copy, and carousel cards.

Pure, dependency-free content + accessors so the same approved copy can be
reused from the Telegram bot, the outreach agent, and any Meta Ads tooling.
The business runs a Ford F-150 plus a drop-off container that is set on-site
and picked back up, serving the Dallas-Fort Worth (DFW) metro.

Nothing here sends anything. It only returns text for a human to copy/send or
to load into Meta Ads Manager.
"""

from typing import Dict, List, Optional

BUSINESS_NAME = "HaulYeah"
SERVICE_AREA = "DFW"

# DFW metro cities used to localize outreach. Lowercase keys for matching.
DFW_CITIES = [
    "Dallas", "Fort Worth", "Arlington", "Plano", "Irving", "Garland",
    "Frisco", "McKinney", "Denton", "Mesquite", "Carrollton", "Richardson",
    "Grand Prairie", "Lewisville", "Allen", "Mansfield", "Euless", "Bedford",
]

# What sets this crew apart, reused across outreach + ads.
_VALUE_PROPS = [
    "F-150 + a drop-off container we leave on-site and haul away when you're done",
    "same-week (often same-day) availability across the DFW metro",
    "you only pay for the space you use — no hidden dump fees",
    "we do the loading and the responsible disposal",
]

# Shared visual style appended to every carousel image prompt so the 5 cards
# look like one branded set. Square (1:1) suits a Meta carousel slot.
_IMAGE_STYLE = (
    "Photorealistic advertising photo, bright natural daylight, clean and "
    "professional, vibrant but realistic colors, 1:1 square composition with "
    "uncluttered space for a text overlay, no visible logos or readable text."
)


# ------------------------------------------------------------------ #
# Outreach — for replying to Marketplace / lead inquiries             #
# ------------------------------------------------------------------ #

def _city_phrase(city: Optional[str]) -> str:
    city = (city or "").strip()
    if city and any(city.lower() == c.lower() for c in DFW_CITIES):
        return f"right here in {city}"
    return "across the DFW area"


def outreach_message(
    job_type: str = "junk removal",
    city: Optional[str] = None,
    include_container: bool = True,
) -> str:
    """Friendly DFW-localized first-touch outreach message.

    `include_container` highlights the drop-off-container option (good for
    multi-day cleanouts / renovations where they fill it over time).
    """
    job_type = (job_type or "junk removal").strip()
    where = _city_phrase(city)

    container_line = (
        " We can also drop a container on-site, leave it while you load at your "
        "own pace, then pick it up — handy for bigger cleanouts and renos."
        if include_container
        else ""
    )
    return (
        f"Hi! Saw your post about {job_type} — we're {BUSINESS_NAME}, a local "
        f"hauling crew working {where}. We run a truck and trailer, handle any "
        f"size load, and do the lifting and disposal for you.{container_line} "
        f"Happy to give you a free quote — what are you looking to get rid of, "
        f"and where are you located?"
    )


def container_pitch(city: Optional[str] = None) -> str:
    """Short pitch focused on the drop-and-pick-up container service."""
    where = _city_phrase(city)
    return (
        f"We drop a container at your place {where}, leave it so you can load on "
        f"your own schedule, then pick it up and haul it off when you're done. You pay for the "
        f"space you use — no surprise dump fees. Want me to check availability?"
    )


# ------------------------------------------------------------------ #
# Meta single-image / video ad copy                                   #
# ------------------------------------------------------------------ #

def meta_ad_copy() -> List[Dict[str, str]]:
    """Ready-to-paste Meta ad variants (primary text / headline / description).

    Field names match Meta Ads Manager so they can be copied straight in, or
    fed to the ads MCP `ads_create_creative` call as object_story_spec fields.
    """
    return [
        {
            "name": "junk_removal_speed",
            "primary_text": (
                "Junk piling up? HaulYeah clears it out fast. 🛻 We bring the "
                "truck and a drop-off container, do all the heavy lifting, and "
                "haul everything to the dump for you — serving all of DFW. "
                "Same-week (often same-day) pickup. Free, no-pressure quotes."
            ),
            "headline": "DFW Junk & Trash Hauling",
            "description": "Free quote • Same-week pickup",
            "cta": "GET_QUOTE",
        },
        {
            "name": "container_dropoff",
            "primary_text": (
                "Doing a cleanout or reno? Skip the dumpster headache. 📦 We drop "
                "a container at your place, leave it so you can load at your own "
                "pace, then pick it up when you're done. You only pay for the "
                "space you use. Proudly serving the DFW metro."
            ),
            "headline": "Drop-Off Container Service — DFW",
            "description": "No hidden dump fees",
            "cta": "GET_QUOTE",
        },
        {
            "name": "cleanout_anything",
            "primary_text": (
                "Garage, estate, foreclosure, or construction debris — if it "
                "needs to go, HaulYeah hauls it. 💪 One call, we load it, you "
                "relax. Honest flat-rate pricing and friendly local crew across "
                "Dallas–Fort Worth."
            ),
            "headline": "We Haul Anything",
            "description": "Flat-rate • Local DFW crew",
            "cta": "MESSAGE_PAGE",
        },
    ]


# ------------------------------------------------------------------ #
# Carousel cards (each card = one image slot in a Meta carousel ad)   #
# ------------------------------------------------------------------ #

def carousel_cards() -> List[Dict[str, str]]:
    """Five carousel cards, one service angle each.

    `headline` is the card title (Meta limit ~40 chars), `body` is suggested
    on-image/caption copy, `image_hint` is a short creative direction, and
    `image_prompt` is a ready-to-paste text-to-image prompt (share brand style
    via `_IMAGE_STYLE`) for whatever generator you use.
    """
    return [
        {
            "headline": "Junk Removal, Done For You",
            "body": "We load it, haul it, and dispose of it. You don't lift a thing.",
            "image_hint": "Crew loading furniture into an F-150 in a driveway",
            "image_prompt": (
                "Two friendly uniformed haulers lifting an old couch and a mattress into "
                "the bed of a clean white Ford F-150 pickup in a suburban driveway, sunny "
                f"morning, neat Texas home in the background. {_IMAGE_STYLE}"
            ),
        },
        {
            "headline": "Drop-Off Container",
            "body": "We drop it, you fill it on your schedule, we pick it up.",
            "image_hint": "Trailer container set on a residential driveway",
            "image_prompt": (
                "A clean open-top dump trailer / roll-off-style container parked on a "
                "residential driveway next to a white Ford F-150, ready to be loaded, tidy "
                f"suburban Texas neighborhood, bright daylight. {_IMAGE_STYLE}"
            ),
        },
        {
            "headline": "Cleanouts of Any Size",
            "body": "Garage, estate, foreclosure, attic — we clear it all out.",
            "image_hint": "Before/after of a cleared-out garage",
            "image_prompt": (
                "Split before-and-after of a residential garage: left side cluttered with "
                "junk and boxes, right side completely clean and empty, same garage, clear "
                f"divider down the middle. {_IMAGE_STYLE}"
            ),
        },
        {
            "headline": "Construction & Reno Debris",
            "body": "Drywall, lumber, old fixtures — hauled away fast.",
            "image_hint": "Truck bed loaded with construction debris",
            "image_prompt": (
                "The bed of a white Ford F-150 neatly loaded with construction debris — "
                "broken drywall, scrap lumber, an old sink — parked outside a home being "
                f"renovated, daytime. {_IMAGE_STYLE}"
            ),
        },
        {
            "headline": "Free Quote, Same-Week Pickup",
            "body": "Serving all of DFW. Message us — we'll get you scheduled.",
            "image_hint": "Smiling crew by the truck with HaulYeah branding",
            "image_prompt": (
                "Two smiling haulers in matching caps standing confidently in front of a "
                "clean white Ford F-150, arms crossed, friendly approachable vibe, suburban "
                f"Dallas-Fort Worth street, blue sky. {_IMAGE_STYLE}"
            ),
        },
    ]


def value_props() -> List[str]:
    """The reusable list of differentiators (outreach + ad bullet points)."""
    return list(_VALUE_PROPS)


# ------------------------------------------------------------------ #
# Meta carousel campaign payload                                      #
# ------------------------------------------------------------------ #

# Map our friendly CTA tokens to Meta's call_to_action types.
_CTA_TYPES = {"GET_QUOTE", "MESSAGE_PAGE", "LEARN_MORE", "CALL_NOW", "BOOK_TRAVEL"}

# DFW metro center (Dallas) — used for the default geo target radius.
_DFW_LAT, _DFW_LNG = 32.7767, -96.7970


def meta_carousel_campaign_spec(
    ad_account_id: str = "AD_ACCOUNT_ID",
    page_id: str = "PAGE_ID",
    link_url: str = "https://example.com/quote",
    daily_budget_cents: int = 1000,
    image_hashes: Optional[List[str]] = None,
) -> Dict:
    """Build the Meta Graph API payloads for the 5-card carousel ad.

    Returns campaign / ad_set / creative / ad request bodies ready to pass to
    `ads_create_campaign`, `ads_create_ad_set`, `ads_create_creative`,
    `ads_create_ad`. Everything is created PAUSED so nothing spends until a human
    activates it. `image_hashes` (one per card, from uploaded creatives) fills
    each child_attachment; placeholders are used when not yet uploaded.
    """
    cards = carousel_cards()
    hashes = image_hashes or [f"IMAGE_HASH_{i + 1}" for i in range(len(cards))]

    child_attachments = [
        {
            "link": link_url,
            "name": card["headline"],          # card title
            "description": card["body"],         # card subtext
            "image_hash": hashes[i] if i < len(hashes) else f"IMAGE_HASH_{i + 1}",
            "call_to_action": {"type": "GET_QUOTE", "value": {"link": link_url}},
        }
        for i, card in enumerate(cards)
    ]

    # Lead with the container message — it's the strongest differentiator.
    primary = next(
        (a["primary_text"] for a in meta_ad_copy() if a["name"] == "container_dropoff"),
        meta_ad_copy()[0]["primary_text"],
    )

    return {
        "campaign": {
            "ad_account_id": ad_account_id,
            "name": f"{BUSINESS_NAME} — {SERVICE_AREA} Carousel",
            "objective": "OUTCOME_TRAFFIC",
            "status": "PAUSED",
            "special_ad_categories": [],
        },
        "ad_set": {
            "ad_account_id": ad_account_id,
            "name": f"{BUSINESS_NAME} — DFW homeowners",
            "daily_budget": daily_budget_cents,
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "LINK_CLICKS",
            "status": "PAUSED",
            "targeting": {
                "geo_locations": {
                    "custom_locations": [
                        {"latitude": _DFW_LAT, "longitude": _DFW_LNG,
                         "radius": 40, "distance_unit": "mile"}
                    ]
                },
                "age_min": 28,
                "age_max": 65,
            },
        },
        "creative": {
            "ad_account_id": ad_account_id,
            "name": f"{BUSINESS_NAME} carousel creative",
            "object_story_spec": {
                "page_id": page_id,
                "link_data": {
                    "link": link_url,
                    "message": primary,
                    "child_attachments": child_attachments,
                    "multi_share_optimized": True,
                    "multi_share_end_card": True,
                },
            },
        },
        "ad": {
            "ad_account_id": ad_account_id,
            "name": f"{BUSINESS_NAME} carousel ad",
            "status": "PAUSED",
        },
    }
