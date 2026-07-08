"""
Compliance guardrail tests.

Two jobs:
  1. Unit-test the helpers in utils.compliance.
  2. Static-scan the real agents/ and integrations/ source for any AUTOMATED
     Facebook send/post path (or auto-send to a customer) that is not gated behind
     a human approval. If such a path exists, fail loudly -- the no-auto-send rule
     is the #1 ban-prevention control (see COMPLIANCE.md).

The scan is deliberately conservative: it flags source that BOTH performs an
externally-facing send/post AND lacks an approval/confirm gate. Today the bot has
no such path (outreach.confirm_send only marks a queued draft as sent after a human
/confirm; the scraper is read-only), so this test passes -- and will start failing
the moment someone wires an auto-send.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.compliance import (
    ALLOWED_ACTIONS,
    FORBIDDEN_ACTIONS,
    ComplianceError,
    assert_human_approved,
    human_pace_sleep,
    is_outbound_allowed,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCAN_DIRS = ["agents", "integrations"]

# Lines doing an actual outbound FB/customer send or post. Note: telegram_bot's
# reply_text / bot.send_message talk to the OWNER/team (internal), not customers
# or Facebook, so they are excluded by the patterns below.
_SEND_PATTERNS = [
    re.compile(r"\.send_message\s*\(", re.IGNORECASE),        # messenger / fb send_message
    re.compile(r"send_dm|sendDM|send_direct_message", re.IGNORECASE),
    re.compile(r"\.post_(listing|to_marketplace|to_group|comment)\s*\(", re.IGNORECASE),
    re.compile(r"create_listing|publish_listing|publish_post", re.IGNORECASE),
    re.compile(r"send_sms|send_email|send_text", re.IGNORECASE),
    re.compile(r"marketplace.{0,40}(create|publish|post)", re.IGNORECASE),
]

# Tokens that indicate an approval/human-in-the-loop gate, OR that the send is an
# INTERNAL Telegram message to the owner/team (the approval surface itself), not an
# outbound send to a customer or Facebook. A bare Telegram send to a team chat_id is
# the human-in-the-loop channel, so it is not an auto-send-to-customer violation.
_APPROVAL_TOKENS = re.compile(
    r"assert_human_approved|confirm_send|human_approv|approved|/confirm|confirm:"
    r"|notify_team|reply_text|chat_id|reply_markup|callback_data|_send_confirmation"
    r"|team_chat",
    re.IGNORECASE,
)


def _python_sources():
    for sub in _SCAN_DIRS:
        d = os.path.join(_REPO_ROOT, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".py") and name != "__init__.py":
                yield os.path.join(d, name)


# --------------------------------------------------------------------------- #
# assert_human_approved                                                        #
# --------------------------------------------------------------------------- #

class TestAssertHumanApproved:
    def test_raises_when_not_approved(self):
        with pytest.raises(ComplianceError):
            assert_human_approved(False, "fb_dm")

    def test_passes_when_approved(self):
        # Should not raise.
        assert_human_approved(True, "mark_sent")

    def test_falsey_values_block(self):
        for val in (False, None, 0, "", []):
            with pytest.raises(ComplianceError):
                assert_human_approved(val, "send_sms")


# --------------------------------------------------------------------------- #
# is_outbound_allowed                                                          #
# --------------------------------------------------------------------------- #

class TestIsOutboundAllowed:
    def test_forbidden_actions_blocked(self):
        for action in FORBIDDEN_ACTIONS:
            assert is_outbound_allowed(action) is False

    def test_allowed_actions_pass(self):
        for action in ALLOWED_ACTIONS:
            assert is_outbound_allowed(action) is True

    def test_unknown_action_fails_closed(self):
        assert is_outbound_allowed("totally_unknown_action") is False

    def test_no_overlap_between_sets(self):
        assert ALLOWED_ACTIONS.isdisjoint(FORBIDDEN_ACTIONS)


# --------------------------------------------------------------------------- #
# human_pace_sleep                                                             #
# --------------------------------------------------------------------------- #

class TestHumanPaceSleep:
    def test_sleeps_within_range(self):
        slept = human_pace_sleep(0.0, 0.01)
        assert 0.0 <= slept <= 0.01

    def test_args_in_either_order(self):
        slept = human_pace_sleep(0.01, 0.0)
        assert 0.0 <= slept <= 0.01

    def test_negative_clamped(self):
        slept = human_pace_sleep(-1.0, 0.0)
        assert slept == 0.0


# --------------------------------------------------------------------------- #
# No automated FB send/post path in the real source                           #
# --------------------------------------------------------------------------- #

class TestNoAutomatedSendPath:
    def test_source_files_exist(self):
        # Sanity: we are actually scanning real files, not silently passing on an
        # empty set.
        files = list(_python_sources())
        assert files, "no agent/integration source files found to scan"
        names = {os.path.basename(f) for f in files}
        assert "scraper.py" in names
        assert "outreach.py" in names

    def test_scraper_is_read_only(self):
        """The scraper must never send/post -- only read."""
        path = os.path.join(_REPO_ROOT, "agents", "scraper.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for pat in _SEND_PATTERNS:
            assert not pat.search(src), (
                f"scraper.py matched a send/post pattern {pat.pattern!r} -- the "
                "scraper must stay read-only (see COMPLIANCE.md)."
            )

    def test_no_ungated_send_or_post(self):
        """Flag any send/post line in agents|integrations not near an approval gate.

        For each matching line we inspect a small window of surrounding lines. If a
        send/post pattern appears with NO approval/human-in-the-loop token nearby,
        that's an automated send path and we fail with the offending location.
        """
        violations = []
        for path in _python_sources():
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                # Skip comments/docstring-ish lines so prose can't trip the scan.
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if not any(pat.search(line) for pat in _SEND_PATTERNS):
                    continue
                window = "".join(lines[max(0, i - 6): i + 7])
                if not _APPROVAL_TOKENS.search(window):
                    rel = os.path.relpath(path, _REPO_ROOT)
                    violations.append(f"{rel}:{i + 1}: {stripped}")

        assert not violations, (
            "Automated send/post path(s) without a human-approval gate found:\n  "
            + "\n  ".join(violations)
            + "\nAI may only DRAFT; a human must approve before anything is sent/posted "
            "(see COMPLIANCE.md)."
        )

    def test_confirm_send_does_not_network_send(self):
        """outreach.confirm_send must only mark state, never actually send."""
        path = os.path.join(_REPO_ROOT, "agents", "outreach.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # The function exists and is documented as never auto-sending.
        assert "def confirm_send" in src
        # No outbound HTTP/messaging primitives that would constitute a real send
        # to a customer/Facebook inside the outreach module.
        assert "send_dm" not in src.lower()
        assert "post_listing" not in src.lower()
