"""
Lead/job ranking — pure, unit-testable scoring helpers (Piece 2).

Scores a scraped lead 0-100 from four signals already present on the lead
dict (see integrations.sheets.COLUMNS and agents.scraper):

  * urgency  — urgency_score (1-10) the scraper assigned from keywords
  * size     — size_score   (1-10) the scraper assigned from keywords
  * recency  — how fresh date_found is (ISO 8601 timestamp)
  * location — proximity to the DFW service area (description + location text)

No I/O, no network, no Sheets — everything here is a pure function so it can
be tested in isolation and reused by the (separate) Telegram /topleads handler.

The Sheets layer stores urgency_score / size_score as strings and reads them
back via get_all_records() (which may return str or int), so every numeric
read here is coerced defensively.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------- #
# Signal weights — sum to 100 so a perfect lead scores 100.                     #
# ---------------------------------------------------------------------------- #
_W_URGENCY = 35
_W_SIZE = 30
_W_RECENCY = 20
_W_LOCATION = 15

# Scraper urgency/size scores are on a 1-10 scale (see agents.scraper).
_RAW_SCORE_MAX = 10

# Recency decay: a lead loses its full recency weight linearly over this many
# hours. Junk-removal leads go cold fast, so a 3-day window is generous.
_RECENCY_FULL_HOURS = 72.0

# DFW service-area location tokens. A lead whose location/description mentions
# any of these is treated as in-area. Kept here (not config) so scoring stays
# pure and deterministic for tests; config.fb_search_location is also honored.
_DFW_AREA_TOKENS = (
    "dallas", "fort worth", "ft worth", "ftworth", "dfw", "arlington",
    "plano", "irving", "garland", "frisco", "mckinney", "mesquite",
    "carrollton", "denton", "richardson", "lewisville", "allen",
    "flower mound", "grand prairie", "euless", "bedford", "hurst",
    "grapevine", "mansfield", "rowlett", "wylie", "cedar hill",
    "desoto", "duncanville", "keller", "coppell", "rockwall",
    "north richland hills", "the colony", "little elm", "prosper",
    "sachse", "addison", "farmers branch", "balch springs", "texas",
    "tx",
)

# Explicitly out-of-area signals — a lead that names a far metro should be
# penalized even if it also matches the broad "texas"/"tx" token.
_OUT_OF_AREA_TOKENS = (
    "houston", "austin", "san antonio", "el paso", "amarillo",
    "lubbock", "corpus christi", "waco", "tyler", "oklahoma",
)


def _coerce_score(value, default: int = 1) -> int:
    """Read a 1-10 raw score that may arrive as int, float, or str."""
    try:
        return max(0, min(_RAW_SCORE_MAX, int(float(value))))
    except (TypeError, ValueError):
        return default


def _parse_ts(value) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp; return tz-aware UTC or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _recency_fraction(date_found, now: Optional[datetime] = None) -> float:
    """0.0-1.0 freshness: 1.0 brand new, decaying linearly to 0 at the window
    edge. Missing/unparseable dates get a neutral-low 0.3 so they aren't
    rewarded for being undated but aren't fully buried either."""
    dt = _parse_ts(date_found)
    if dt is None:
        return 0.3
    now = now or datetime.now(timezone.utc)
    age_hours = (now - dt).total_seconds() / 3600.0
    if age_hours <= 0:
        return 1.0
    if age_hours >= _RECENCY_FULL_HOURS:
        return 0.0
    return 1.0 - (age_hours / _RECENCY_FULL_HOURS)


def _location_fraction(lead: Dict, service_area: str = "") -> Tuple[float, str]:
    """0.0-1.0 proximity to the DFW service area, plus a short label.

    Looks at the lead's location + description text (and the configured
    service_area home base) for in-area / out-of-area tokens.
    """
    haystack = " ".join(
        str(lead.get(f, "")) for f in ("location", "description", "job_type")
    ).lower()
    base = (service_area or "").lower()

    out_hit = next((t for t in _OUT_OF_AREA_TOKENS if t in haystack), None)
    in_hit = next((t for t in _DFW_AREA_TOKENS if t in haystack), None)

    if out_hit and not in_hit:
        return 0.0, f"out of area ({out_hit})"
    # If the configured home base name appears, treat as a strong in-area match.
    if base and base in haystack:
        return 1.0, "in DFW area"
    if in_hit:
        # "tx"/"texas" alone is weaker than a named DFW city.
        if in_hit in ("tx", "texas"):
            return 0.6, "in Texas"
        return 1.0, f"in DFW area ({in_hit})"
    return 0.5, "location unknown"


def score_lead(lead: Dict, now: Optional[datetime] = None, service_area: str = "") -> Tuple[int, str]:
    """Score a single lead 0-100 and return (score, human-readable reason).

    Pure: depends only on the lead dict (+ optional `now` for deterministic
    tests and `service_area` home-base string). Never touches I/O.
    """
    now = now or datetime.now(timezone.utc)

    urgency = _coerce_score(lead.get("urgency_score"), default=1)
    size = _coerce_score(lead.get("size_score"), default=1)
    recency_frac = _recency_fraction(lead.get("date_found"), now=now)
    loc_frac, loc_label = _location_fraction(lead, service_area=service_area)

    urgency_pts = (urgency / _RAW_SCORE_MAX) * _W_URGENCY
    size_pts = (size / _RAW_SCORE_MAX) * _W_SIZE
    recency_pts = recency_frac * _W_RECENCY
    location_pts = loc_frac * _W_LOCATION

    total = int(round(urgency_pts + size_pts + recency_pts + location_pts))
    total = max(0, min(100, total))

    # Build a compact reason from the dominant signals.
    urgency_word = "urgent" if urgency >= 7 else ("warm" if urgency >= 4 else "low-urgency")
    size_word = "big job" if size >= 7 else ("mid job" if size >= 5 else "small job")
    if recency_frac >= 0.8:
        fresh_word = "fresh"
    elif recency_frac >= 0.4:
        fresh_word = "recent"
    else:
        fresh_word = "aging"

    reason = f"{urgency_word}, {size_word}, {fresh_word}, {loc_label}"
    return total, reason


def rank_leads(
    leads: List[Dict],
    top_n: int = 5,
    now: Optional[datetime] = None,
    service_area: str = "",
) -> List[Dict]:
    """Score every lead and return the top_n as a new list (descending score).

    Each returned item is a shallow copy of the input lead with two extra
    keys added: `score` (int 0-100) and `score_reason` (str). The input list
    is not mutated. Ties break by recency (fresher first), then by id for a
    stable, deterministic order.
    """
    now = now or datetime.now(timezone.utc)
    scored: List[Dict] = []
    for lead in leads:
        score, reason = score_lead(lead, now=now, service_area=service_area)
        enriched = dict(lead)
        enriched["score"] = score
        enriched["score_reason"] = reason
        scored.append(enriched)

    def _sort_key(item: Dict):
        ts = _parse_ts(item.get("date_found"))
        recency_key = ts.timestamp() if ts else 0.0
        return (-item["score"], -recency_key, str(item.get("id", "")))

    scored.sort(key=_sort_key)
    if top_n is not None and top_n >= 0:
        return scored[:top_n]
    return scored


def _suggested_action(lead: Dict) -> str:
    """A short next-step hint for the digest, based on score + contact info."""
    score = lead.get("score", 0)
    has_phone = bool(str(lead.get("contact", "")).strip())
    if score >= 70:
        return "draft outreach now" + (" (/quicksend ready)" if has_phone else "")
    if score >= 45:
        return "review + draft outreach"
    return "low priority — skim later"


def format_topleads_digest(ranked: List[Dict]) -> str:
    """Render a ranked list (output of rank_leads) into a Telegram-friendly
    digest. One line per lead: id, score, short reason, suggested action."""
    if not ranked:
        return "No new leads to rank right now. ✅"

    lines = [f"🏆 Top {len(ranked)} new leads:"]
    for i, lead in enumerate(ranked, start=1):
        lead_id = str(lead.get("id", "?"))
        score = lead.get("score", 0)
        reason = lead.get("score_reason", "")
        action = _suggested_action(lead)
        lines.append(f"{i}. [{lead_id}] {score}/100 — {reason}\n   → {action}")
    return "\n".join(lines)
