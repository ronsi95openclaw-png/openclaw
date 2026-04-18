"""Tests for agents/lifeos_checkin.py — LifeOS check-in state machine."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, call


def _reload_modules(tmp_path: Path):
    """Reload lifeos_agent (with redirected paths) then lifeos_checkin.

    Order matters: agent must be re-imported and patched first so that the
    checkin module picks up the redirected paths when it does
    `from agents.lifeos_agent import ...`.
    """
    # 1. Evict both modules from the cache
    for key in list(sys.modules):
        if "lifeos_agent" in key or "lifeos_checkin" in key:
            del sys.modules[key]

    # 2. Re-import agent and redirect its paths
    import agents.lifeos_agent as agent_m

    agent_m._LIFEOS_DIR  = tmp_path / "lifeos"
    agent_m._INTAKE_FILE = tmp_path / "lifeos" / "intake.json"
    agent_m._SCORES_FILE = tmp_path / "lifeos" / "scores.json"
    agent_m._LOGS_DIR    = tmp_path / "lifeos" / "daily_logs"

    # 3. Now re-import checkin — it will use the already-imported agent module
    #    from sys.modules, which already has redirected paths.
    import agents.lifeos_checkin as checkin_m

    # 4. Clear any leftover in-memory state
    checkin_m._STATES.clear()

    return agent_m, checkin_m


# ── State lifecycle ───────────────────────────────────────────────────────────

def test_start_morning_creates_state(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_morning_checkin(1001)
    state = c.get_pending_state(1001)
    assert state is not None
    assert state["type"] == "morning"
    assert state["step"] == 0


def test_start_evening_creates_state(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_evening_checkin(2002)
    state = c.get_pending_state(2002)
    assert state is not None
    assert state["type"] == "evening"
    assert state["step"] == 0


def test_no_state_returns_none(tmp_path):
    _, c = _reload_modules(tmp_path)
    assert c.get_pending_state(9999) is None


def test_clear_state_removes_entry(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_morning_checkin(3003)
    c.clear_state(3003)
    assert c.get_pending_state(3003) is None


# ── No active check-in ────────────────────────────────────────────────────────

def test_no_active_checkin(tmp_path):
    _, c = _reload_modules(tmp_path)
    done, reply = c.handle_checkin_reply(9999, "hello")
    assert done is True
    assert "morning" in reply.lower() or "evening" in reply.lower()


# ── Morning flow ──────────────────────────────────────────────────────────────

def test_handle_morning_flow_returns_next_question(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_morning_checkin(1001)
    done, reply = c.handle_checkin_reply(1001, "Ship the feature")
    assert done is False
    assert reply == c.MORNING_QUESTIONS[1]


def test_morning_flow_completes(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_morning_checkin(1001)
    # Step 0 — plan
    done, _ = c.handle_checkin_reply(1001, "Write tests")
    assert done is False
    # Step 1 — gym
    done, _ = c.handle_checkin_reply(1001, "no")
    assert done is False
    # Step 2 — top 3
    done, reply = c.handle_checkin_reply(1001, "1. tests 2. PR 3. review")
    assert done is True
    assert "saved" in reply.lower()
    # State should be cleared
    assert c.get_pending_state(1001) is None


def test_morning_gym_yes_adds_score(tmp_path):
    agent_m, c = _reload_modules(tmp_path)
    c.start_morning_checkin(1001)
    c.handle_checkin_reply(1001, "Build feature")
    c.handle_checkin_reply(1001, "yes")
    c.handle_checkin_reply(1001, "1. gym 2. work 3. sleep")
    scores = agent_m.get_scores()
    events = [h["event"] for h in scores["history"]]
    assert "workout" in events


def test_morning_gym_no_no_score(tmp_path):
    agent_m, c = _reload_modules(tmp_path)
    c.start_morning_checkin(1001)
    c.handle_checkin_reply(1001, "Rest day")
    c.handle_checkin_reply(1001, "no")
    c.handle_checkin_reply(1001, "1. work 2. read 3. sleep")
    scores = agent_m.get_scores()
    events = [h["event"] for h in scores["history"]]
    assert "workout" not in events


# ── Evening flow ──────────────────────────────────────────────────────────────

def test_evening_flow_completes(tmp_path):
    _, c = _reload_modules(tmp_path)
    c.start_evening_checkin(2002)
    # Step 0 — completed
    done, _ = c.handle_checkin_reply(2002, "Finished PR and gym")
    assert done is False
    # Step 1 — diet
    done, _ = c.handle_checkin_reply(2002, "yes")
    assert done is False
    # Step 2 — expenses
    done, _ = c.handle_checkin_reply(2002, "35.00")
    assert done is False
    # Step 3 — income
    done, _ = c.handle_checkin_reply(2002, "0")
    assert done is False
    # Step 4 — energy
    done, reply = c.handle_checkin_reply(2002, "8")
    assert done is True
    assert "saved" in reply.lower()
    assert c.get_pending_state(2002) is None


def test_evening_diet_ok_adds_score(tmp_path):
    agent_m, c = _reload_modules(tmp_path)
    c.start_evening_checkin(2002)
    c.handle_checkin_reply(2002, "All done")
    c.handle_checkin_reply(2002, "yes")   # diet ok
    c.handle_checkin_reply(2002, "0")     # no expenses
    c.handle_checkin_reply(2002, "0")     # no income
    c.handle_checkin_reply(2002, "7")     # energy
    scores = agent_m.get_scores()
    events = [h["event"] for h in scores["history"]]
    assert "diet_adherence" in events


def test_evening_expense_logged(tmp_path):
    agent_m, c = _reload_modules(tmp_path)
    c.start_evening_checkin(2002)
    c.handle_checkin_reply(2002, "Busy day")
    c.handle_checkin_reply(2002, "no")    # diet
    c.handle_checkin_reply(2002, "45.50") # expenses
    c.handle_checkin_reply(2002, "0")     # income
    c.handle_checkin_reply(2002, "6")     # energy
    # Verify expense was written to today's log
    log = agent_m.get_today_log()
    assert "expenses" in log
    amounts = [e["amount"] for e in log["expenses"]]
    assert 45.50 in amounts
