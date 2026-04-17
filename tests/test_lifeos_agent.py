"""Tests for agents/lifeos_agent.py — LifeOS data layer."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _reload_module(tmp_path: Path):
    """Import (or re-import) lifeos_agent with paths redirected to tmp_path."""
    # Remove cached module so monkeypatching takes effect on re-import
    sys.modules.pop("agents.lifeos_agent", None)
    sys.modules.pop("lifeos_agent", None)

    import agents.lifeos_agent as m

    # Redirect all path constants to tmp_path
    m._LIFEOS_DIR   = tmp_path / "lifeos"
    m._INTAKE_FILE  = tmp_path / "lifeos" / "intake.json"
    m._SCORES_FILE  = tmp_path / "lifeos" / "scores.json"
    m._LOGS_DIR     = tmp_path / "lifeos" / "daily_logs"

    return m


# ──────────────────────────────────────────────────────────────────────────────
# Intake profile
# ──────────────────────────────────────────────────────────────────────────────

def test_save_and_load_intake(tmp_path):
    m = _reload_module(tmp_path)
    profile = {"name": "Alice", "goal_weight": 65.0, "monthly_income": 3000}
    m.save_intake(profile)
    loaded = m.load_intake()
    assert loaded["name"] == "Alice"
    assert loaded["goal_weight"] == 65.0
    assert loaded["monthly_income"] == 3000


def test_save_intake_merges(tmp_path):
    m = _reload_module(tmp_path)
    m.save_intake({"name": "Alice"})
    m.save_intake({"goal_weight": 65.0})
    loaded = m.load_intake()
    assert loaded["name"] == "Alice"
    assert loaded["goal_weight"] == 65.0


def test_load_intake_empty(tmp_path):
    m = _reload_module(tmp_path)
    assert m.load_intake() == {}


def test_log_weight(tmp_path):
    m = _reload_module(tmp_path)
    m.log_weight(80.5)
    intake = m.load_intake()
    assert intake["current_weight"] == 80.5
    assert len(intake["weight_history"]) == 1
    assert intake["weight_history"][0]["kg"] == 80.5


# ──────────────────────────────────────────────────────────────────────────────
# Daily logs
# ──────────────────────────────────────────────────────────────────────────────

def test_log_daily_entry(tmp_path):
    m = _reload_module(tmp_path)
    m.log_daily_entry("morning", {"gym": True, "mood": "good"})
    log = m.get_today_log()
    assert "morning" in log
    assert log["morning"]["gym"] is True
    assert log["morning"]["mood"] == "good"
    assert "timestamp" in log["morning"]


def test_log_daily_entry_evening(tmp_path):
    m = _reload_module(tmp_path)
    m.log_daily_entry("morning", {"gym": False})
    m.log_daily_entry("evening", {"reflection": "productive day"})
    log = m.get_today_log()
    assert "morning" in log
    assert "evening" in log
    assert log["evening"]["reflection"] == "productive day"


def test_log_expense(tmp_path):
    m = _reload_module(tmp_path)
    m.log_expense(25.50, "food", "lunch")
    log = m.get_today_log()
    assert "expenses" in log
    assert len(log["expenses"]) == 1
    exp = log["expenses"][0]
    assert exp["amount"] == 25.50
    assert exp["category"] == "food"
    assert exp["description"] == "lunch"
    assert "timestamp" in exp


def test_log_expense_multiple(tmp_path):
    m = _reload_module(tmp_path)
    m.log_expense(10.0, "transport", "bus")
    m.log_expense(50.0, "groceries", "weekly shop")
    log = m.get_today_log()
    assert len(log["expenses"]) == 2


def test_get_today_log_empty(tmp_path):
    m = _reload_module(tmp_path)
    assert m.get_today_log() == {}


def test_get_log_for_date(tmp_path):
    m = _reload_module(tmp_path)
    # Write directly to a specific date file
    specific_day = "2026-01-15"
    path = m._LOGS_DIR / f"{specific_day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps({"morning": {"gym": True}}), encoding="utf-8")

    log = m.get_log_for_date("2026-01-15")
    assert log["morning"]["gym"] is True


def test_get_recent_logs(tmp_path):
    m = _reload_module(tmp_path)
    # Log something today
    m.log_daily_entry("morning", {"gym": True})
    logs = m.get_recent_logs(7)
    assert isinstance(logs, list)
    assert len(logs) >= 1
    assert "date" in logs[0]


# ──────────────────────────────────────────────────────────────────────────────
# Gamification
# ──────────────────────────────────────────────────────────────────────────────

def test_add_score_points(tmp_path):
    m = _reload_module(tmp_path)
    m.add_score("workout")      # +10
    m.add_score("deep_work")    # +15
    scores = m.get_scores()
    assert scores["total"] == 25


def test_score_penalty(tmp_path):
    m = _reload_module(tmp_path)
    m.add_score("missed_workout")   # -10
    scores = m.get_scores()
    assert scores["total"] == -10


def test_add_score_custom_points(tmp_path):
    m = _reload_module(tmp_path)
    m.add_score("bonus", points=50)
    scores = m.get_scores()
    assert scores["total"] == 50


def test_add_score_unknown_event_zero(tmp_path):
    m = _reload_module(tmp_path)
    m.add_score("unknown_event")
    scores = m.get_scores()
    assert scores["total"] == 0


def test_add_score_history(tmp_path):
    m = _reload_module(tmp_path)
    m.add_score("workout")
    scores = m.get_scores()
    assert len(scores["history"]) == 1
    assert scores["history"][0]["event"] == "workout"
    assert scores["history"][0]["points"] == 10


def test_streak_increments(tmp_path):
    m = _reload_module(tmp_path)
    result = m.record_active_day()
    assert result["streak"] >= 1
    assert result["last_active_date"] is not None


def test_streak_no_double_count(tmp_path):
    m = _reload_module(tmp_path)
    r1 = m.record_active_day()
    r2 = m.record_active_day()
    assert r1["streak"] == r2["streak"]
    assert r1["last_active_date"] == r2["last_active_date"]


def test_get_scores_defaults(tmp_path):
    m = _reload_module(tmp_path)
    scores = m.get_scores()
    assert scores["total"] == 0
    assert scores["streak"] == 0
    assert scores["last_active_date"] is None
    assert scores["history"] == []


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────

def test_get_dashboard_data(tmp_path):
    m = _reload_module(tmp_path)
    m.save_intake({
        "current_weight": 80.0,
        "goal_weight": 70.0,
        "monthly_income": 4000,
        "total_debt": 5000,
        "investments": 1000,
        "coach_mode": "STRICT",
    })
    m.log_daily_entry("morning", {"gym": True})
    m.log_daily_entry("evening", {"reflection": "good day"})
    m.add_score("workout")

    data = m.get_dashboard_data()

    # Check top-level keys
    assert "fitness" in data
    assert "finance" in data
    assert "habits" in data
    assert "profile" in data

    # Fitness
    assert data["fitness"]["weight"] == 80.0
    assert data["fitness"]["goal_weight"] == 70.0
    assert data["fitness"]["workouts"] >= 1

    # Finance
    assert data["finance"]["income"] == 4000
    assert data["finance"]["debt"] == 5000

    # Habits
    assert data["habits"]["score"] == 10
    assert isinstance(data["habits"]["streak"], int)
    assert isinstance(data["habits"]["completionRate"], int)

    # Profile
    assert data["profile"]["setup_done"] is True
    assert data["profile"]["coach_mode"] == "STRICT"


def test_get_dashboard_data_empty(tmp_path):
    m = _reload_module(tmp_path)
    data = m.get_dashboard_data()
    assert "fitness" in data
    assert "finance" in data
    assert "habits" in data
    assert "profile" in data
    assert data["profile"]["setup_done"] is False
    assert data["habits"]["score"] == 0
