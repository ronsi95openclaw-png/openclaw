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
    on-image/caption copy, `image_hint` guides the creative for each slot.
    """
    return [
        {
            "headline": "Junk Removal, Done For You",
            "body": "We load it, haul it, and dispose of it. You don't lift a thing.",
            "image_hint": "Crew loading furniture into an F-150 in a driveway",
        },
        {
            "headline": "Drop-Off Container",
            "body": "We drop it, you fill it on your schedule, we pick it up.",
            "image_hint": "Trailer container set on a residential driveway",
        },
        {
            "headline": "Cleanouts of Any Size",
            "body": "Garage, estate, foreclosure, attic — we clear it all out.",
            "image_hint": "Before/after of a cleared-out garage",
        },
        {
            "headline": "Construction & Reno Debris",
            "body": "Drywall, lumber, old fixtures — hauled away fast.",
            "image_hint": "Truck bed loaded with construction debris",
        },
        {
            "headline": "Free Quote, Same-Week Pickup",
            "body": "Serving all of DFW. Message us — we'll get you scheduled.",
            "image_hint": "Smiling crew by the truck with HaulYeah branding",
        },
    ]


def value_props() -> List[str]:
    """The reusable list of differentiators (outreach + ad bullet points)."""
    return list(_VALUE_PROPS)
