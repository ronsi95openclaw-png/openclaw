import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infra.watchdog import DEFAULT_MARKERS, bot_is_running, format_down_alert


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


class TestFormatDownAlert:
    def test_contains_bot_name(self):
        assert "ClawBot" in format_down_alert("ClawBot", "2026-05-29 07:00")

    def test_contains_timestamp(self):
        assert "2026-05-29 07:00" in format_down_alert("ClawBot", "2026-05-29 07:00")

    def test_says_down(self):
        assert "DOWN" in format_down_alert("ClawBot", "2026-05-29 07:00").upper()
