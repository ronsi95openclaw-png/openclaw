"""LifeOS data layer for ClawBot.

Persists user profile, daily check-in logs, gamification scores, and streaks
to data/lifeos/ as JSON files. No external dependencies beyond stdlib.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT       = Path(__file__).parent.parent
_LIFEOS_DIR = _ROOT / "data" / "lifeos"
_INTAKE_FILE = _LIFEOS_DIR / "intake.json"
_SCORES_FILE = _LIFEOS_DIR / "scores.json"
_LOGS_DIR    = _LIFEOS_DIR / "daily_logs"

# ── Points table ─────────────────────────────────────────────────────────────

POINT_TABLE: Dict[str, int] = {
    "workout":         +10,
    "diet_adherence":  +10,
    "deep_work":       +15,
    "expense_tracked": +5,
    "missed_workout":  -10,
    "overspending":    -10,
    "skipped_priority":-15,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Intake profile ────────────────────────────────────────────────────────────

def save_intake(profile: Dict[str, Any]) -> None:
    """Persist the user's intake profile (merge into existing)."""
    existing = _read_json(_INTAKE_FILE, {})
    existing.update(profile)
    _write_json(_INTAKE_FILE, existing)


def load_intake() -> Dict[str, Any]:
    """Return the stored intake profile, or {} if not yet set up."""
    return _read_json(_INTAKE_FILE, {})


def log_weight(kg: float) -> None:
    """Update current_weight in the intake profile."""
    profile = load_intake()
    profile["current_weight"] = kg
    profile.setdefault("weight_history", []).append(
        {"date": _today(), "kg": kg}
    )
    _write_json(_INTAKE_FILE, profile)


# ── Daily logs ────────────────────────────────────────────────────────────────

def _log_path(day: Optional[str] = None) -> Path:
    return _LOGS_DIR / f"{day or _today()}.json"


def log_daily_entry(section: str, data: Dict[str, Any]) -> None:
    """Merge morning or evening check-in data into today's log.

    section: "morning" | "evening"
    data: arbitrary dict of check-in fields (merged into any existing section data)
    """
    path    = _log_path()
    entry   = _read_json(path, {})
    ts      = datetime.now(timezone.utc).isoformat()
    existing_section = entry.get(section, {})
    existing_section.update({"timestamp": ts, **data})
    entry[section] = existing_section
    _write_json(path, entry)


def log_expense(amount: float, category: str, description: str = "") -> None:
    """Append an expense to today's log."""
    path   = _log_path()
    entry  = _read_json(path, {})
    entry.setdefault("expenses", []).append({
        "amount":      amount,
        "category":    category,
        "description": description,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })
    _write_json(path, entry)


def log_income(amount: float, source: str, description: str = "") -> None:
    """Append an income entry to today's log and the persistent income log."""
    path  = _log_path()
    entry = _read_json(path, {})
    entry.setdefault("income", []).append({
        "amount":      amount,
        "source":      source,
        "description": description,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })
    _write_json(path, entry)

    # Also append to data/income_log.json for the dashboard clip-economy view
    income_log_path = _ROOT / "data" / "income_log.json"
    income_log = _read_json(income_log_path, [])
    income_log.append({
        "amount":      amount,
        "source":      source,
        "description": description,
        "date":        _today(),
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })
    _write_json(income_log_path, income_log)


def get_today_log() -> Dict[str, Any]:
    return _read_json(_log_path(), {})


def get_log_for_date(day: str) -> Dict[str, Any]:
    """day: YYYY-MM-DD"""
    return _read_json(_log_path(day), {})


def get_recent_logs(n: int = 7) -> List[Dict[str, Any]]:
    """Return the last n days of logs as a list of dicts."""
    results = []
    for i in range(n):
        day  = (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat()
        log  = get_log_for_date(day)
        if log:
            results.append({"date": day, **log})
    return results


# ── Gamification ──────────────────────────────────────────────────────────────

def _load_scores() -> Dict[str, Any]:
    return _read_json(_SCORES_FILE, {
        "total": 0,
        "streak": 0,
        "last_active_date": None,
        "history": [],
    })


def add_score(event: str, points: Optional[int] = None) -> Dict[str, Any]:
    """Award or deduct points. Uses POINT_TABLE if points is None."""
    if points is None:
        points = POINT_TABLE.get(event, 0)
    scores = _load_scores()
    scores["total"] += points
    scores["history"].append({
        "event":   event,
        "points":  points,
        "date":    _today(),
    })
    _write_json(_SCORES_FILE, scores)
    return scores


def record_active_day() -> Dict[str, Any]:
    """Call once per day when any positive activity is logged. Updates streak."""
    scores = _load_scores()
    today  = _today()
    last   = scores.get("last_active_date")

    if last == today:
        return scores  # already recorded today

    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if last == yesterday:
        scores["streak"] = scores.get("streak", 0) + 1
    elif last != today:
        scores["streak"] = 1  # streak broken or first day

    scores["last_active_date"] = today
    _write_json(_SCORES_FILE, scores)
    return scores


def get_scores() -> Dict[str, Any]:
    return _load_scores()


# ── Dashboard summary ─────────────────────────────────────────────────────────

def get_dashboard_data() -> Dict[str, Any]:
    """Return the full /api/life-dashboard payload."""
    intake  = load_intake()
    scores  = get_scores()
    today   = get_today_log()
    recent  = get_recent_logs(7)

    # Expense total for today
    today_expenses = sum(e["amount"] for e in today.get("expenses", []))

    # Completion rate: days with both morning+evening check-ins / last 7
    complete_days = sum(
        1 for log in recent
        if "morning" in log and "evening" in log
    )
    completion_rate = round(complete_days / 7 * 100) if recent else 0

    return {
        "fitness": {
            "weight":      intake.get("current_weight", ""),
            "goal_weight": intake.get("goal_weight", ""),
            "workouts":    sum(
                1 for log in recent
                if log.get("morning", {}).get("gym") is True
            ),
        },
        "finance": {
            "income":      intake.get("monthly_income", ""),
            "expenses":    today_expenses,
            "debt":        intake.get("total_debt", ""),
            "investments": intake.get("investments", ""),
        },
        "habits": {
            "score":           scores["total"],
            "streak":          scores["streak"],
            "completionRate":  completion_rate,
        },
        "profile": {
            "coach_mode":  intake.get("coach_mode", "STRICT"),
            "setup_done":  bool(intake),
        },
    }
