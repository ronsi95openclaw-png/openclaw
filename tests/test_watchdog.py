import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infra.watchdog import (
    DEFAULT_MARKERS,
    bot_is_running,
    format_down_alert,
    format_escalation_alert,
    format_recovery_alert,
    transition,
)


class TestBotIsRunning:
    def test_empty_process_list_means_down(self):
        assert bot_is_running([], DEFAULT_MARKERS) is False

    def test_detects_module_invocation(self):
        cmdlines = [r"C:\path\python.exe -m content.receiver"]
        assert bot_is_running(cmdlines, DEFAULT_MARKERS) is True

    def test_detects_script_path_invocation(self):
        cmdlines = [r"python.exe content\receiver.py"]
        assert bot_is_running(cmdlines, DEFAULT_MARKERS) is True

    def test_ignores_unrelated_python_processes(self):
        cmdlines = ["python.exe -m pip install foo", "python.exe other_thing.py"]
        assert bot_is_running(cmdlines, DEFAULT_MARKERS) is False

    def test_handles_none_entries(self):
        cmdlines = [None, r"python.exe -m content.receiver"]
        assert bot_is_running(cmdlines, DEFAULT_MARKERS) is True

    def test_custom_marker(self):
        assert bot_is_running(["python myapp.py"], ["myapp.py"]) is True

    def test_ignores_dash_c_verifier_referencing_module(self):
        """A `python -c` verifier that mentions content.receiver must NOT count as the bot."""
        cmdlines = [
            'python.exe -c "import importlib; importlib.import_module(\'content.receiver\')"',
        ]
        assert bot_is_running(cmdlines, DEFAULT_MARKERS) is False


class TestFormatDownAlert:
    def test_contains_bot_name(self):
        assert "ClawBot" in format_down_alert("ClawBot", "2026-05-29 07:00")

    def test_contains_timestamp(self):
        assert "2026-05-29 07:00" in format_down_alert("ClawBot", "2026-05-29 07:00")

    def test_says_down(self):
        assert "DOWN" in format_down_alert("ClawBot", "2026-05-29 07:00").upper()


class TestFormatEscalationAndRecovery:
    def test_escalation_contains_bot_name_and_keyword(self):
        msg = format_escalation_alert("ClawBot", 75)
        assert "ClawBot" in msg
        assert "ESCALATION" in msg

    def test_recovery_contains_bot_name_and_keyword(self):
        msg = format_recovery_alert("ClawBot", 12)
        assert "ClawBot" in msg
        assert "RECOVERED" in msg


class TestTransition:
    def test_healthy_stays_healthy_emits_nothing(self):
        now = datetime(2026, 5, 30, 12, 0, 0)
        new_state, kind = transition(True, {}, now, 60)
        assert kind is None
        assert new_state == {}

    def test_first_down_emits_down_and_records_timestamps(self):
        now = datetime(2026, 5, 30, 12, 0, 0)
        new_state, kind = transition(False, {}, now, 60)
        assert kind == "down"
        assert new_state["down_since"] == now.isoformat()
        assert new_state["alert_sent_at"] == now.isoformat()

    def test_in_cooldown_still_down_emits_none(self):
        first_seen = datetime(2026, 5, 30, 12, 0, 0)
        state = {
            "down_since": first_seen.isoformat(),
            "alert_sent_at": first_seen.isoformat(),
        }
        # 30 minutes later, cooldown is 60 → still silent
        now = first_seen + timedelta(minutes=30)
        new_state, kind = transition(False, state, now, 60)
        assert kind is None
        # State preserved unchanged
        assert new_state == state

    def test_post_cooldown_still_down_emits_escalation(self):
        first_seen = datetime(2026, 5, 30, 12, 0, 0)
        state = {
            "down_since": first_seen.isoformat(),
            "alert_sent_at": first_seen.isoformat(),
        }
        now = first_seen + timedelta(minutes=75)  # >60m cooldown
        new_state, kind = transition(False, state, now, 60)
        assert kind == "escalation"
        # down_since unchanged, alert_sent_at advanced to now
        assert new_state["down_since"] == first_seen.isoformat()
        assert new_state["alert_sent_at"] == now.isoformat()

    def test_recovery_emits_recovery_and_clears_state(self):
        first_seen = datetime(2026, 5, 30, 12, 0, 0)
        state = {
            "down_since": first_seen.isoformat(),
            "alert_sent_at": first_seen.isoformat(),
        }
        now = first_seen + timedelta(minutes=20)
        new_state, kind = transition(True, state, now, 60)
        assert kind == "recovery"
        assert new_state == {}

    def test_escalation_exactly_at_cooldown_boundary(self):
        """At exactly cooldown_minutes elapsed, escalate (>= boundary)."""
        first_seen = datetime(2026, 5, 30, 12, 0, 0)
        state = {
            "down_since": first_seen.isoformat(),
            "alert_sent_at": first_seen.isoformat(),
        }
        now = first_seen + timedelta(minutes=60)
        new_state, kind = transition(False, state, now, 60)
        assert kind == "escalation"
        assert new_state["alert_sent_at"] == now.isoformat()
