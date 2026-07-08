# LifeOS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed a full LifeOS system into ClawBot — morning/evening Telegram check-ins, gamification scoring, expense/weight logging, and a `/api/life-dashboard` JSON endpoint wired into the Flask dashboard.

**Architecture:** A new `agents/lifeos_agent.py` owns all data persistence (reads/writes `data/lifeos/`). A `agents/lifeos_checkin.py` manages multi-step conversational check-in flows using a simple state machine keyed on `chat_id`. Telegram commands in `content/receiver.py` handle user interaction. APScheduler in `core/scheduler.py` fires morning/evening check-ins automatically. The Flask dashboard gains a `/api/life-dashboard` read-only endpoint.

**Tech Stack:** Python 3.x, python-telegram-bot ≥ 21.10, APScheduler, Flask, JSON file store (no new deps required)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agents/lifeos_agent.py` | **Create** | Intake profile, daily log CRUD, gamification scoring, streak tracking |
| `agents/lifeos_checkin.py` | **Create** | Morning/evening check-in state machines, pending state per chat_id |
| `content/receiver.py` | **Modify** | Add 8 new cmd_ handlers: `/lifeos`, `/morning`, `/evening`, `/score`, `/logweight`, `/logexpense`, `/lifesetup`, `/lifemode` |
| `core/scheduler.py` | **Modify** | Add `schedule_lifeos_checkins()`, `_fire_morning_checkin()`, `_fire_evening_checkin()` |
| `dashboard/app.py` | **Modify** | Add `/api/life-dashboard` endpoint |
| `data/lifeos/` | **Create (runtime)** | `intake.json`, `scores.json`, `daily_logs/YYYY-MM-DD.json` |

---

## Task 1: Create `agents/lifeos_agent.py` — Data Layer

**Files:**
- Create: `agents/lifeos_agent.py`
- Test: `tests/test_lifeos_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_lifeos_agent.py
import json, pytest
from pathlib import Path

@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import agents.lifeos_agent as la
    monkeypatch.setattr(la, "_LIFEOS_DIR", tmp_path / "lifeos")
    monkeypatch.setattr(la, "_INTAKE_FILE", tmp_path / "lifeos" / "intake.json")
    monkeypatch.setattr(la, "_SCORES_FILE", tmp_path / "lifeos" / "scores.json")
    monkeypatch.setattr(la, "_LOGS_DIR", tmp_path / "lifeos" / "daily_logs")

def test_save_and_load_intake():
    from agents.lifeos_agent import save_intake, load_intake
    profile = {"weight": 85, "goal_weight": 75, "coach_mode": "STRICT"}
    save_intake(profile)
    assert load_intake()["weight"] == 85

def test_log_daily_entry():
    from agents.lifeos_agent import log_daily_entry, get_today_log
    log_daily_entry("morning", {"plan": "gym + deep work", "gym": True, "top3": ["gym", "budget", "code"]})
    entry = get_today_log()
    assert entry["morning"]["gym"] is True

def test_add_score_points():
    from agents.lifeos_agent import add_score, get_scores
    add_score("workout", +10)
    add_score("deep_work", +15)
    s = get_scores()
    assert s["total"] == 25
    assert s["streak"] >= 0

def test_score_penalty():
    from agents.lifeos_agent import add_score, get_scores
    add_score("missed_workout", -10)
    s = get_scores()
    assert s["total"] == -10

def test_streak_increments():
    from agents.lifeos_agent import record_active_day, get_scores
    record_active_day()
    s = get_scores()
    assert s["streak"] >= 1

def test_log_expense():
    from agents.lifeos_agent import log_expense, get_today_log
    log_expense(12.50, "food", "lunch")
    entry = get_today_log()
    assert entry["expenses"][0]["amount"] == 12.50
    assert entry["expenses"][0]["category"] == "food"

def test_log_weight():
    from agents.lifeos_agent import log_weight, load_intake
    log_weight(84.5)
    assert load_intake()["current_weight"] == 84.5
```

- [ ] **Step 2: Run tests to verify they all fail**

```
pytest tests/test_lifeos_agent.py -v
```
Expected: 7 FAILED (ImportError / AttributeError — module doesn't exist yet)

- [ ] **Step 3: Implement `agents/lifeos_agent.py`**

```python
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
    """Persist the user's intake profile (overwrite)."""
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
    """Write morning or evening check-in data for today.

    section: "morning" | "evening"
    data: arbitrary dict of check-in fields
    """
    path    = _log_path()
    entry   = _read_json(path, {})
    ts      = datetime.now(timezone.utc).isoformat()
    entry[section] = {"timestamp": ts, **data}
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


def get_today_log() -> Dict[str, Any]:
    return _read_json(_log_path(), {})


def get_log_for_date(day: str) -> Dict[str, Any]:
    """day: YYYY-MM-DD"""
    return _read_json(_log_path(day), {})


def get_recent_logs(n: int = 7) -> List[Dict[str, Any]]:
    """Return the last n days of logs as a list of dicts."""
    results = []
    for i in range(n):
        day  = (date.today() - timedelta(days=i)).isoformat()
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
    """Award or deduct points.  Uses POINT_TABLE if points is None."""
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

    yesterday = (date.today() - timedelta(days=1)).isoformat()
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
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_lifeos_agent.py -v
```
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/lifeos_agent.py tests/test_lifeos_agent.py
git commit -m "feat: LifeOS data layer — intake, daily logs, gamification, scores"
```

---

## Task 2: Create `agents/lifeos_checkin.py` — Check-in State Machine

**Files:**
- Create: `agents/lifeos_checkin.py`
- Test: `tests/test_lifeos_checkin.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_lifeos_checkin.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import agents.lifeos_agent as la
    monkeypatch.setattr(la, "_LIFEOS_DIR", tmp_path / "lifeos")
    monkeypatch.setattr(la, "_INTAKE_FILE", tmp_path / "lifeos" / "intake.json")
    monkeypatch.setattr(la, "_SCORES_FILE", tmp_path / "lifeos" / "scores.json")
    monkeypatch.setattr(la, "_LOGS_DIR", tmp_path / "lifeos" / "daily_logs")

def test_start_morning_creates_state():
    from agents.lifeos_checkin import start_morning_checkin, get_pending_state
    start_morning_checkin(123)
    state = get_pending_state(123)
    assert state is not None
    assert state["type"] == "morning"
    assert state["step"] == 0

def test_start_evening_creates_state():
    from agents.lifeos_checkin import start_evening_checkin, get_pending_state
    start_evening_checkin(456)
    state = get_pending_state(456)
    assert state["type"] == "evening"

def test_handle_morning_flow_returns_next_question():
    from agents.lifeos_checkin import start_morning_checkin, handle_checkin_reply
    start_morning_checkin(789)
    done, reply = handle_checkin_reply(789, "gym + deep work + calls")
    assert done is False
    assert len(reply) > 0

def test_morning_flow_completes():
    from agents.lifeos_checkin import start_morning_checkin, handle_checkin_reply
    start_morning_checkin(101)
    handle_checkin_reply(101, "gym + code")         # plan
    handle_checkin_reply(101, "yes")                # gym scheduled?
    done, reply = handle_checkin_reply(101, "1. gym 2. budget 3. code")  # top 3
    assert done is True
    assert "saved" in reply.lower() or "morning" in reply.lower()

def test_evening_flow_completes():
    from agents.lifeos_checkin import start_evening_checkin, handle_checkin_reply
    start_evening_checkin(202)
    handle_checkin_reply(202, "finished all 3 priorities")  # completed?
    handle_checkin_reply(202, "yes")                        # diet?
    handle_checkin_reply(202, "45")                         # expenses
    handle_checkin_reply(202, "0")                          # income earned
    done, reply = handle_checkin_reply(202, "8")            # energy
    assert done is True

def test_no_state_returns_none():
    from agents.lifeos_checkin import get_pending_state
    assert get_pending_state(99999) is None

def test_clear_state_removes_entry():
    from agents.lifeos_checkin import start_morning_checkin, clear_state, get_pending_state
    start_morning_checkin(303)
    clear_state(303)
    assert get_pending_state(303) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_lifeos_checkin.py -v
```
Expected: 7 FAILED (ImportError)

- [ ] **Step 3: Implement `agents/lifeos_checkin.py`**

```python
"""LifeOS check-in state machine for ClawBot.

Manages multi-step morning and evening Telegram check-in conversations.
State is kept in-memory (dict keyed on chat_id) — survives within a
bot session but resets on restart (intentional: daily check-ins are ephemeral).
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from agents.lifeos_agent import (
    add_score,
    log_daily_entry,
    log_expense,
    record_active_day,
)

# ── In-memory state store (chat_id → state dict) ─────────────────────────────

_STATES: Dict[int, dict] = {}

MORNING_QUESTIONS = [
    "Good morning! What's your plan for today?",
    "Is the gym scheduled today? (yes/no)",
    "What are your top 3 priorities today? (e.g. 1. gym 2. budget review 3. deep work)",
]

EVENING_QUESTIONS = [
    "Evening check-in. What did you complete today?",
    "Did you follow your diet today? (yes/no)",
    "Total expenses today? (number only, e.g. 45.50)",
    "Any income earned today? (number only, 0 if none)",
    "Energy level today? (1–10)",
]


# ── Public API ────────────────────────────────────────────────────────────────

def start_morning_checkin(chat_id: int) -> str:
    """Start the morning check-in for chat_id. Returns the first question."""
    _STATES[chat_id] = {"type": "morning", "step": 0, "data": {}}
    return MORNING_QUESTIONS[0]


def start_evening_checkin(chat_id: int) -> str:
    """Start the evening check-in for chat_id. Returns the first question."""
    _STATES[chat_id] = {"type": "evening", "step": 0, "data": {}}
    return EVENING_QUESTIONS[0]


def get_pending_state(chat_id: int) -> Optional[dict]:
    """Return the current check-in state for chat_id, or None."""
    return _STATES.get(chat_id)


def clear_state(chat_id: int) -> None:
    """Remove any pending check-in state for chat_id."""
    _STATES.pop(chat_id, None)


def handle_checkin_reply(chat_id: int, text: str) -> Tuple[bool, str]:
    """Process a user reply for an active check-in.

    Returns:
        (done, reply_text)
        done=True when the check-in is complete and state has been cleared.
    """
    state = _STATES.get(chat_id)
    if not state:
        return True, "No active check-in. Send /morning or /evening to start."

    checkin_type = state["type"]
    step         = state["step"]
    data         = state["data"]

    if checkin_type == "morning":
        return _handle_morning_step(chat_id, state, step, data, text)
    else:
        return _handle_evening_step(chat_id, state, step, data, text)


# ── Morning flow ──────────────────────────────────────────────────────────────

def _handle_morning_step(
    chat_id: int, state: dict, step: int, data: dict, text: str
) -> Tuple[bool, str]:
    if step == 0:
        data["plan"] = text
        state["step"] = 1
        return False, MORNING_QUESTIONS[1]

    elif step == 1:
        data["gym"] = text.strip().lower() in ("yes", "y", "1", "true")
        state["step"] = 2
        return False, MORNING_QUESTIONS[2]

    elif step == 2:
        data["top3"] = text
        log_daily_entry("morning", data)
        record_active_day()
        if data.get("gym"):
            add_score("workout")
        _STATES.pop(chat_id, None)

        gym_line = "Gym: scheduled ✅" if data["gym"] else "Gym: not scheduled"
        return True, (
            f"Morning check-in saved.\n\n"
            f"Plan: {data['plan']}\n"
            f"{gym_line}\n"
            f"Top 3: {data['top3']}\n\n"
            f"Let's get it. Execute."
        )

    return True, "Unexpected state. Check-in reset."


# ── Evening flow ──────────────────────────────────────────────────────────────

def _handle_evening_step(
    chat_id: int, state: dict, step: int, data: dict, text: str
) -> Tuple[bool, str]:
    if step == 0:
        data["completed"] = text
        state["step"] = 1
        return False, EVENING_QUESTIONS[1]

    elif step == 1:
        data["diet_ok"] = text.strip().lower() in ("yes", "y", "1", "true")
        state["step"] = 2
        return False, EVENING_QUESTIONS[2]

    elif step == 2:
        try:
            data["expenses"] = float(text.strip().replace(",", ""))
        except ValueError:
            data["expenses"] = 0.0
        state["step"] = 3
        return False, EVENING_QUESTIONS[3]

    elif step == 3:
        try:
            data["income_earned"] = float(text.strip().replace(",", ""))
        except ValueError:
            data["income_earned"] = 0.0
        state["step"] = 4
        return False, EVENING_QUESTIONS[4]

    elif step == 4:
        try:
            data["energy"] = int(text.strip())
        except ValueError:
            data["energy"] = 5

        # Persist
        log_daily_entry("evening", data)
        if data.get("expenses", 0) > 0:
            log_expense(data["expenses"], "untracked", "evening check-in total")
            add_score("expense_tracked")
        if data.get("diet_ok"):
            add_score("diet_adherence")
        record_active_day()
        _STATES.pop(chat_id, None)

        energy_bar = "🔥" * min(data["energy"], 10)
        return True, (
            f"Evening check-in saved.\n\n"
            f"Completed: {data['completed']}\n"
            f"Diet: {'✅' if data['diet_ok'] else '❌'}\n"
            f"Expenses: ${data['expenses']:.2f}\n"
            f"Income earned: ${data['income_earned']:.2f}\n"
            f"Energy: {data['energy']}/10 {energy_bar}\n\n"
            f"Rest well. Tomorrow we go again."
        )

    return True, "Unexpected state. Check-in reset."
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_lifeos_checkin.py -v
```
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add agents/lifeos_checkin.py tests/test_lifeos_checkin.py
git commit -m "feat: LifeOS check-in state machine — morning/evening flows"
```

---

## Task 3: Add LifeOS Commands to `content/receiver.py`

**Files:**
- Modify: `content/receiver.py`

The plan adds 8 new handlers. Add them after the existing `/remind` handlers. Also update `cmd_start` to list the new commands, and wire them into `main()`.

- [ ] **Step 1: Add imports at the top of receiver.py** (after the existing agent imports, around line 92)

Find the block:
```python
from skills.second_brain import (
```

After its closing `)`, add:
```python
from agents.lifeos_agent import (
    add_score,
    get_dashboard_data,
    get_scores,
    load_intake,
    log_expense,
    log_weight,
    save_intake,
)
from agents.lifeos_checkin import (
    clear_state,
    get_pending_state,
    handle_checkin_reply,
    start_evening_checkin,
    start_morning_checkin,
)
```

- [ ] **Step 2: Add the 8 command handlers** (paste after `cmd_cancel` handler, search for `async def cmd_cancel`)

```python
# ── LifeOS ─────────────────────────────────────────────────────────────────────

async def cmd_lifeos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show LifeOS status summary."""
    if not is_authorized(update.effective_chat.id):
        return
    data = get_dashboard_data()
    intake = load_intake()
    if not intake:
        await _safe_reply(update.message,
            "LifeOS is not set up yet.\n\nRun /lifesetup to enter your profile.")
        return
    f = data["fitness"]
    fin = data["finance"]
    h = data["habits"]
    lines = [
        "<b>LifeOS Dashboard</b>\n",
        f"<b>Fitness</b>",
        f"  Weight: {f['weight']} kg  →  Goal: {f['goal_weight']} kg",
        f"  Workouts this week: {f['workouts']}",
        "",
        f"<b>Finance</b>",
        f"  Monthly income: ${fin['income']}",
        f"  Debt: ${fin['debt']}",
        f"  Today's expenses: ${fin['expenses']:.2f}",
        "",
        f"<b>Habits</b>",
        f"  Score: {h['score']} pts  |  Streak: {h['streak']} days",
        f"  Completion rate (7d): {h['completionRate']}%",
        "",
        f"Coach mode: {data['profile']['coach_mode']}",
        "",
        "/morning — start morning check-in",
        "/evening — start evening check-in",
        "/score   — gamification leaderboard",
    ]
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the morning check-in flow."""
    if not is_authorized(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    first_q = start_morning_checkin(chat_id)
    await _safe_reply(update.message, f"<b>Morning Check-in</b>\n\n{first_q}")


async def cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the evening check-in flow."""
    if not is_authorized(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    first_q = start_evening_checkin(chat_id)
    await _safe_reply(update.message, f"<b>Evening Check-in</b>\n\n{first_q}")


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show gamification score + streak."""
    if not is_authorized(update.effective_chat.id):
        return
    s = get_scores()
    streak_bar = "🔥" * min(s["streak"], 14)
    lines = [
        "<b>LifeOS Score</b>\n",
        f"Total points:  <b>{s['total']}</b>",
        f"Current streak: <b>{s['streak']} days</b>  {streak_bar}",
        "",
        "<b>Points table</b>",
        "  +10  workout completed",
        "  +10  diet adherence",
        "  +15  deep work session",
        "  +5   expense tracked",
        "  -10  missed workout",
        "  -10  overspending",
        "  -15  skipped priorities",
    ]
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_logweight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/logweight 84.5 — log today's weight in kg."""
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await _safe_reply(update.message, "Usage: /logweight [kg]\nExample: /logweight 84.5")
        return
    try:
        kg = float(args[0])
    except ValueError:
        await _safe_reply(update.message, "Invalid number. Example: /logweight 84.5")
        return
    log_weight(kg)
    intake = load_intake()
    goal = intake.get("goal_weight", "?")
    diff = round(kg - float(goal), 1) if goal != "?" else "?"
    await _safe_reply(update.message,
        f"Weight logged: <b>{kg} kg</b>\nGoal: {goal} kg  |  Gap: {diff} kg")


async def cmd_logexpense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/logexpense 12.50 food lunch — log an expense."""
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args or len(args) < 2:
        await _safe_reply(update.message,
            "Usage: /logexpense [amount] [category] [description]\n"
            "Example: /logexpense 12.50 food lunch")
        return
    try:
        amount = float(args[0])
    except ValueError:
        await _safe_reply(update.message, "Invalid amount. Example: /logexpense 12.50 food lunch")
        return
    category    = args[1] if len(args) > 1 else "other"
    description = " ".join(args[2:]) if len(args) > 2 else ""
    log_expense(amount, category, description)
    add_score("expense_tracked")
    await _safe_reply(update.message,
        f"Expense logged: <b>${amount:.2f}</b> — {category}"
        + (f" ({description})" if description else "")
        + "\n+5 pts")


async def cmd_lifesetup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifesetup key=value — save intake profile fields."""
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await _safe_reply(update.message,
            "<b>LifeOS Setup</b>\n\n"
            "Use: /lifesetup key=value [key=value ...]\n\n"
            "Available keys:\n"
            "  weight=85           current weight (kg)\n"
            "  goal_weight=75      target weight (kg)\n"
            "  monthly_income=5000\n"
            "  total_debt=15000\n"
            "  investments=2000\n"
            "  coach_mode=STRICT   (STRICT / BALANCED / CHILL)\n\n"
            "Example:\n"
            "/lifesetup weight=85 goal_weight=75 monthly_income=5000")
        return
    profile: dict = {}
    errors: list = []
    for arg in args:
        if "=" not in arg:
            errors.append(f"Skipped '{arg}' (no '=' found)")
            continue
        key, _, val = arg.partition("=")
        key = key.strip()
        val = val.strip()
        # Try casting numeric fields
        if key in ("weight", "goal_weight", "monthly_income", "total_debt", "investments"):
            try:
                profile[key] = float(val)
            except ValueError:
                errors.append(f"'{key}' must be a number, got '{val}'")
        else:
            profile[key] = val
    if profile:
        save_intake(profile)
    lines = ["<b>LifeOS profile updated:</b>"]
    for k, v in profile.items():
        lines.append(f"  {k} = {v}")
    if errors:
        lines.append("\n<b>Warnings:</b>")
        lines.extend(f"  {e}" for e in errors)
    await _safe_reply(update.message, "\n".join(lines))


async def cmd_lifemode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifemode strict|balanced|chill — change coach personality."""
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    valid = {"strict": "STRICT", "balanced": "BALANCED", "chill": "CHILL"}
    if not args or args[0].lower() not in valid:
        await _safe_reply(update.message,
            "Usage: /lifemode strict|balanced|chill")
        return
    mode = valid[args[0].lower()]
    save_intake({"coach_mode": mode})
    await _safe_reply(update.message, f"Coach mode set to <b>{mode}</b>.")
```

- [ ] **Step 3: Wire check-in replies into the free-text message handler**

Find `async def handle_message` (the catch-all text handler). At the **very top** of the function body (before the LLM call), add:

```python
    # ── LifeOS check-in intercept ─────────────────────────────────────────────
    chat_id = update.effective_chat.id
    if get_pending_state(chat_id):
        done, reply = handle_checkin_reply(chat_id, update.message.text or "")
        await _safe_reply(update.message, reply)
        return
```

- [ ] **Step 4: Register all 8 handlers in `main()`**

Find the block where handlers are added (search for `app.add_handler(CommandHandler("remind"`). After the existing handlers, add:

```python
    app.add_handler(CommandHandler("lifeos",      cmd_lifeos))
    app.add_handler(CommandHandler("morning",     cmd_morning))
    app.add_handler(CommandHandler("evening",     cmd_evening))
    app.add_handler(CommandHandler("score",       cmd_score))
    app.add_handler(CommandHandler("logweight",   cmd_logweight))
    app.add_handler(CommandHandler("logexpense",  cmd_logexpense))
    app.add_handler(CommandHandler("lifesetup",   cmd_lifesetup))
    app.add_handler(CommandHandler("lifemode",    cmd_lifemode))
```

- [ ] **Step 5: Update `/start` help text** — add a LifeOS section in `cmd_start`:

Find `"<b>⚙️ System:</b>\n"` in `cmd_start` and insert before it:

```python
        "<b>🏆 LifeOS:</b>\n"
        "  /lifeos            — dashboard summary\n"
        "  /morning           — morning check-in\n"
        "  /evening           — evening check-in\n"
        "  /score             — points + streak\n"
        "  /logweight [kg]    — log weight\n"
        "  /logexpense [amt] [cat] — log expense\n"
        "  /lifesetup         — configure profile\n"
        "  /lifemode [mode]   — set coach mode\n\n"
```

- [ ] **Step 6: Syntax check**

```
python -m py_compile content/receiver.py && echo OK
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add content/receiver.py
git commit -m "feat: LifeOS Telegram commands — 8 handlers + check-in intercept"
```

---

## Task 4: Add Scheduled Morning/Evening Check-ins to `core/scheduler.py`

**Files:**
- Modify: `core/scheduler.py`

- [ ] **Step 1: Add the two scheduled job functions** (paste after `reload_autotrade` at the end of the file)

```python
# ---------------------------------------------------------------------------
# LifeOS daily check-in jobs
# ---------------------------------------------------------------------------

_MORNING_CHECKIN_JOB = "lifeos_morning_checkin"
_EVENING_CHECKIN_JOB = "lifeos_evening_checkin"
_LIFEOS_CONFIG_FILE  = _DATA_DIR / "lifeos_schedule.json"


def _load_lifeos_schedule() -> dict:
    if _LIFEOS_CONFIG_FILE.exists():
        try:
            return json.loads(_LIFEOS_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False, "chat_id": None, "morning_time": "07:00", "evening_time": "20:00"}


def _save_lifeos_schedule(cfg: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LIFEOS_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


async def _fire_morning_checkin() -> None:
    cfg = _load_lifeos_schedule()
    if not cfg.get("enabled") or not cfg.get("chat_id"):
        return
    from agents.lifeos_checkin import start_morning_checkin
    first_q = start_morning_checkin(cfg["chat_id"])
    if _send_fn:
        await _send_fn(cfg["chat_id"], f"<b>Morning Check-in</b>\n\n{first_q}")


async def _fire_evening_checkin() -> None:
    cfg = _load_lifeos_schedule()
    if not cfg.get("enabled") or not cfg.get("chat_id"):
        return
    from agents.lifeos_checkin import start_evening_checkin
    first_q = start_evening_checkin(cfg["chat_id"])
    if _send_fn:
        await _send_fn(cfg["chat_id"], f"<b>Evening Check-in</b>\n\n{first_q}")


def enable_lifeos_schedule(
    chat_id: int,
    morning_time: str = "07:00",
    evening_time: str = "20:00",
) -> dict:
    """Enable automatic morning + evening check-ins for chat_id.

    Times are UTC HH:MM strings.
    """
    cfg = {
        "enabled":      True,
        "chat_id":      chat_id,
        "morning_time": morning_time,
        "evening_time": evening_time,
    }
    _save_lifeos_schedule(cfg)
    _register_lifeos_jobs(cfg)
    return cfg


def disable_lifeos_schedule() -> None:
    cfg = _load_lifeos_schedule()
    cfg["enabled"] = False
    _save_lifeos_schedule(cfg)
    if _scheduler:
        for job_id in (_MORNING_CHECKIN_JOB, _EVENING_CHECKIN_JOB):
            if _scheduler.get_job(job_id):
                _scheduler.remove_job(job_id)


def _register_lifeos_jobs(cfg: dict) -> None:
    if not _scheduler:
        return
    mh, mm = cfg["morning_time"].split(":")
    eh, em = cfg["evening_time"].split(":")
    _scheduler.add_job(
        _fire_morning_checkin,
        CronTrigger(hour=int(mh), minute=int(mm), timezone="UTC"),
        id=_MORNING_CHECKIN_JOB,
        replace_existing=True,
    )
    _scheduler.add_job(
        _fire_evening_checkin,
        CronTrigger(hour=int(eh), minute=int(em), timezone="UTC"),
        id=_EVENING_CHECKIN_JOB,
        replace_existing=True,
    )


def reload_lifeos_schedule() -> None:
    """Re-register LifeOS jobs after bot restart."""
    cfg = _load_lifeos_schedule()
    if cfg.get("enabled") and cfg.get("chat_id"):
        _register_lifeos_jobs(cfg)
```

- [ ] **Step 2: Call `reload_lifeos_schedule()` in `start_scheduler()`**

Find:
```python
    _reload_from_disk()
    return _scheduler
```
Change to:
```python
    _reload_from_disk()
    reload_lifeos_schedule()
    return _scheduler
```

- [ ] **Step 3: Add `/lifeschedule` command to receiver.py** (paste after `cmd_lifemode`)

```python
async def cmd_lifeschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lifeschedule on 07:00 20:00 | off — configure auto check-ins (UTC)."""
    if not is_authorized(update.effective_chat.id):
        return
    from core.scheduler import enable_lifeos_schedule, disable_lifeos_schedule
    args = context.args
    if not args:
        await _safe_reply(update.message,
            "Usage:\n"
            "  /lifeschedule on [morning_UTC] [evening_UTC]\n"
            "  /lifeschedule off\n\n"
            "Example: /lifeschedule on 07:00 20:00")
        return
    if args[0].lower() == "off":
        disable_lifeos_schedule()
        await _safe_reply(update.message, "LifeOS scheduled check-ins disabled.")
        return
    morning = args[1] if len(args) > 1 else "07:00"
    evening = args[2] if len(args) > 2 else "20:00"
    cfg = enable_lifeos_schedule(update.effective_chat.id, morning, evening)
    await _safe_reply(update.message,
        f"LifeOS check-ins scheduled:\n"
        f"  Morning: {cfg['morning_time']} UTC\n"
        f"  Evening: {cfg['evening_time']} UTC")
```

Also register it in `main()`:
```python
    app.add_handler(CommandHandler("lifeschedule", cmd_lifeschedule))
```

- [ ] **Step 4: Syntax check both files**

```
python -m py_compile core/scheduler.py && python -m py_compile content/receiver.py && echo OK
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add core/scheduler.py content/receiver.py
git commit -m "feat: LifeOS scheduled morning/evening check-ins via APScheduler"
```

---

## Task 5: Add `/api/life-dashboard` to `dashboard/app.py`

**Files:**
- Modify: `dashboard/app.py`

- [ ] **Step 1: Add the endpoint** (paste after the last `@app.route` block, before `if __name__ == "__main__":`)

```python
@app.route("/api/life-dashboard")
def api_life_dashboard():
    """LifeOS metrics endpoint — fitness, finance, habits."""
    try:
        from agents.lifeos_agent import get_dashboard_data
        return jsonify(get_dashboard_data())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
```

- [ ] **Step 2: Syntax check**

```
python -m py_compile dashboard/app.py && echo OK
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: /api/life-dashboard endpoint for LifeOS metrics"
```

---

## Task 6: Full Syntax Check + End-to-End Smoke Test

- [ ] **Step 1: Syntax-check all touched files**

```
python -m py_compile agents/lifeos_agent.py agents/lifeos_checkin.py core/scheduler.py content/receiver.py dashboard/app.py && echo ALL OK
```
Expected: `ALL OK`

- [ ] **Step 2: Run all LifeOS tests**

```
pytest tests/test_lifeos_agent.py tests/test_lifeos_checkin.py -v
```
Expected: 14 PASSED

- [ ] **Step 3: Smoke test data layer manually**

```python
python -c "
from agents.lifeos_agent import save_intake, load_intake, get_dashboard_data
save_intake({'weight': 85, 'goal_weight': 75, 'monthly_income': 5000, 'total_debt': 10000, 'coach_mode': 'STRICT'})
print(load_intake())
print(get_dashboard_data())
"
```
Expected: dict with fitness/finance/habits keys, no exceptions.

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "feat: LifeOS integration complete — data layer, check-ins, scheduler, dashboard API"
```

---

## Self-Review

**Spec coverage:**
- [x] Morning/evening Telegram check-ins → Task 2 + Task 3
- [x] Gamification points + penalties + streaks → Task 1
- [x] Expense logging → Task 1 (`log_expense`) + Task 3 (`/logexpense`)
- [x] Weight logging → Task 1 (`log_weight`) + Task 3 (`/logweight`)
- [x] Intake profile → Task 1 (`save_intake`) + Task 3 (`/lifesetup`)
- [x] Coach mode → Task 3 (`/lifemode`)
- [x] Scheduled auto check-ins → Task 4
- [x] Dashboard API → Task 5
- [x] Check-in reply intercept in free-text handler → Task 3 Step 3

**Placeholder scan:** No TBD, TODO, or "similar to" references found.

**Type consistency:** `log_daily_entry`, `add_score`, `record_active_day`, `get_scores`, `load_intake` names are consistent across all tasks.
