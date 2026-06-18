"""
Paid Facebook Ads — thin Graph Ads API client for HaulYeah.

Builds and (optionally) creates a paid hauling-service ad through the standard
Meta funnel: campaign -> ad set -> creative -> ad. Credentials come from the
consolidated root .env via config: FB_AD_ACCOUNT_ID, FB_PAGE_ID, FB_ACCESS_TOKEN.

This module can ALSO be driven by the Meta Ads MCP server from chat — the
payload builders here mirror the Graph API shapes the MCP tools expect, so an
operator can review a prepared payload in Telegram and then have the MCP create
the campaign/ad set/creative/ad. This module is the code path; the MCP is the
conversational path. Both default to a safe, no-spend posture.

CRITICAL SAFETY
---------------
Default behavior is DRY-RUN. When ``config.dry_run`` is true OR any required
credential is missing, NO live API call is made — the exact payload that WOULD
be POSTed is returned/logged instead. This module never spends money in tests
or without explicit, fully-credentialed, non-dry-run operation.
"""

import logging
from typing import Any, Dict, List, Optional

import requests

from config import config

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
_GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Objective + optimization for a local lead-gen hauling service.
_OBJECTIVE = "OUTCOME_LEADS"
_OPTIMIZATION_GOAL = "LEAD_GENERATION"
_BILLING_EVENT = "IMPRESSIONS"

# Sensible default ad copy for an F150 junk-hauling service.
DEFAULT_AD_COPY = (
    "Need junk gone? HaulYeah! We haul away trash, old furniture, appliances, "
    "yard debris and full cleanouts with our F150 and trailer. Fast, friendly, "
    "and affordable — same-week pickup. Message us for a free quote!"
)

# Minimum daily budget Meta enforces is ~$1.00; budgets are sent in cents.
_MIN_DAILY_BUDGET_USD = 1.0


def _creds_ready() -> bool:
    """True only when every credential needed for a live call is present."""
    return bool(config.fb_ad_account_id and config.fb_page_id and config.fb_access_token)


def _act_path() -> str:
    acct = config.fb_ad_account_id
    # Graph API expects the ad account id prefixed with "act_".
    if acct and not acct.startswith("act_"):
        acct = f"act_{acct}"
    return acct


def _usd_to_cents(amount_usd: float) -> int:
    return int(round(max(amount_usd, _MIN_DAILY_BUDGET_USD) * 100))


# --------------------------------------------------------------------------- #
# Payload builders (pure — no network, easy to unit test)                      #
# --------------------------------------------------------------------------- #

def build_campaign(name: str = "HaulYeah — Junk Removal Leads") -> Dict[str, Any]:
    """Campaign payload for a lead-generation hauling campaign (starts PAUSED)."""
    return {
        "name": name,
        "objective": _OBJECTIVE,
        "status": "PAUSED",
        "special_ad_categories": [],
    }


def build_ad_set(
    campaign_id: str,
    daily_budget: float,
    location: str,
    radius_miles: int,
    name: str = "HaulYeah — Local Service Area",
) -> Dict[str, Any]:
    """Ad set payload: budget, local radius targeting, optimization. Starts PAUSED."""
    targeting: Dict[str, Any] = {
        "geo_locations": {
            "custom_locations": [
                {
                    # `location` is a human label here; for a precise radius the
                    # caller (or the MCP) supplies lat/lng. We pass the label
                    # through under `name` so the prepared payload is reviewable.
                    "name": location,
                    "radius": radius_miles,
                    "distance_unit": "mile",
                }
            ]
        },
    }
    return {
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": _usd_to_cents(daily_budget),
        "billing_event": _BILLING_EVENT,
        "optimization_goal": _OPTIMIZATION_GOAL,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": targeting,
        "status": "PAUSED",
    }


def build_creative(
    copy: str,
    image_url: str,
    name: str = "HaulYeah — Service Ad Creative",
) -> Dict[str, Any]:
    """Ad creative payload using a single-image link ad pointing at the page."""
    return {
        "name": name,
        "object_story_spec": {
            "page_id": config.fb_page_id,
            "link_data": {
                "message": copy,
                "link": f"https://www.facebook.com/{config.fb_page_id}",
                "picture": image_url,
                "call_to_action": {"type": "MESSAGE_PAGE"},
            },
        },
    }


def build_ad(
    ad_set_id: str,
    creative_id: str,
    name: str = "HaulYeah — Service Ad",
) -> Dict[str, Any]:
    """Ad payload tying an ad set to a creative. Starts PAUSED."""
    return {
        "name": name,
        "adset_id": ad_set_id,
        "creative": {"creative_id": creative_id},
        "status": "PAUSED",
    }


# --------------------------------------------------------------------------- #
# Low-level POST helper (guarded)                                              #
# --------------------------------------------------------------------------- #

def _post(node: str, payload: Dict[str, Any], dry_run: Optional[bool] = None) -> Dict[str, Any]:
    """POST a payload to a Graph API node under the ad account.

    Returns a dict describing the result. In dry-run / missing-creds mode the
    result contains the prepared request without any network call being made.
    """
    is_dry = config.dry_run if dry_run is None else dry_run
    url = f"{_GRAPH_BASE}/{node}"

    if is_dry or not _creds_ready():
        reason = "dry_run" if is_dry else "missing_credentials"
        logger.info("[DRY-RUN fb_ads] Would POST %s -> %s | reason=%s", node, payload, reason)
        return {
            "dry_run": True,
            "reason": reason,
            "url": url,
            "payload": payload,
        }

    body = {**payload, "access_token": config.fb_access_token}
    resp = requests.post(url, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    logger.info("fb_ads POST %s ok -> %s", node, data.get("id", data))
    return {"dry_run": False, "url": url, "response": data}


# --------------------------------------------------------------------------- #
# High-level entry point                                                       #
# --------------------------------------------------------------------------- #

def post_service_ad(
    copy: Optional[str] = None,
    image_url: str = "",
    daily_budget: float = 10.0,
    location: Optional[str] = None,
    radius_miles: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create (or, in dry-run, prepare) a full paid hauling-service ad.

    Builds campaign -> ad set -> creative -> ad. In DRY-RUN or when credentials
    are missing, returns the full set of prepared payloads WITHOUT spending a
    cent. In live mode, each stage's returned id feeds the next stage.

    Returns a dict with a ``dry_run`` flag and a ``stages`` list, each entry
    containing the node, payload, and (live only) the API response.
    """
    copy = copy or DEFAULT_AD_COPY
    location = location if location is not None else (config.fb_search_location or "Local")
    radius_miles = radius_miles if radius_miles is not None else config.fb_search_radius
    is_dry = config.dry_run if dry_run is None else dry_run

    act = _act_path()
    stages: List[Dict[str, Any]] = []

    # Stage 1: campaign
    campaign_payload = build_campaign()
    campaign_res = _post(f"{act}/campaigns", campaign_payload, dry_run=is_dry)
    stages.append({"stage": "campaign", "node": f"{act}/campaigns", "result": campaign_res})
    campaign_id = campaign_res.get("response", {}).get("id", "DRYRUN_CAMPAIGN_ID")

    # Stage 2: ad set
    ad_set_payload = build_ad_set(campaign_id, daily_budget, location, radius_miles)
    ad_set_res = _post(f"{act}/adsets", ad_set_payload, dry_run=is_dry)
    stages.append({"stage": "ad_set", "node": f"{act}/adsets", "result": ad_set_res})
    ad_set_id = ad_set_res.get("response", {}).get("id", "DRYRUN_ADSET_ID")

    # Stage 3: creative
    creative_payload = build_creative(copy, image_url)
    creative_res = _post(f"{act}/adcreatives", creative_payload, dry_run=is_dry)
    stages.append({"stage": "creative", "node": f"{act}/adcreatives", "result": creative_res})
    creative_id = creative_res.get("response", {}).get("id", "DRYRUN_CREATIVE_ID")

    # Stage 4: ad
    ad_payload = build_ad(ad_set_id, creative_id)
    ad_res = _post(f"{act}/ads", ad_payload, dry_run=is_dry)
    stages.append({"stage": "ad", "node": f"{act}/ads", "result": ad_res})

    result = {
        "dry_run": is_dry or not _creds_ready(),
        "creds_ready": _creds_ready(),
        "daily_budget_usd": max(daily_budget, _MIN_DAILY_BUDGET_USD),
        "location": location,
        "radius_miles": radius_miles,
        "stages": stages,
    }
    if result["dry_run"]:
        logger.info("[DRY-RUN fb_ads] Prepared paid service ad (no spend).")
    return result


def summarize_for_telegram(result: Dict[str, Any]) -> str:
    """Render a post_service_ad() result as a concise Telegram-ready summary."""
    mode = "DRY-RUN (no spend)" if result.get("dry_run") else "LIVE"
    lines = [
        f"*Paid FB Ad — {mode}*",
        f"Budget: ${result.get('daily_budget_usd', '?')}/day",
        f"Area: {result.get('location', '?')} ({result.get('radius_miles', '?')} mi)",
        "",
        "Funnel: campaign -> ad set -> creative -> ad",
    ]
    for stage in result.get("stages", []):
        res = stage.get("result", {})
        if res.get("dry_run"):
            lines.append(f"• {stage['stage']}: prepared ({res.get('reason', 'dry_run')})")
        else:
            ad_id = res.get("response", {}).get("id", "?")
            lines.append(f"• {stage['stage']}: created `{ad_id}`")
    if result.get("dry_run"):
        lines.append("\nNo live API call made. Confirm to run live (non-dry-run).")
    return "\n".join(lines)
