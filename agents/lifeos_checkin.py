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
