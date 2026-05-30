import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.outreach import _maybe_append_quote


class TestMaybeAppendQuote:
    def _set_flag(self, value):
        os.environ["OUTREACH_INCLUDE_QUOTE"] = value

    def _clear_flag(self):
        os.environ.pop("OUTREACH_INCLUDE_QUOTE", None)

    def test_disabled_by_default(self):
        self._clear_flag()
        original = "Hi! Want a free quote?"
        assert _maybe_append_quote(original, "some description") == original

    def test_disabled_explicit_false(self):
        self._set_flag("false")
        original = "Hi! Want a free quote?"
        try:
            assert _maybe_append_quote(original, "couch and a chair") == original
        finally:
            self._clear_flag()

    def test_enabled_appends_range_for_small_job(self):
        self._set_flag("true")
        try:
            msg = _maybe_append_quote("Hello!", "just a few items, couple chairs")
            # "couple" / "chair" trigger quarter tier ($199-$299)
            assert "Hello!" in msg
            assert "$199-$299" in msg
        finally:
            self._clear_flag()

    def test_enabled_appends_minimum_for_unmatched(self):
        self._set_flag("true")
        try:
            msg = _maybe_append_quote("Hello!", "")
            # minimum tier price range $89-$189
            assert "$89-$189" in msg
            assert "confirmed on-site" in msg
        finally:
            self._clear_flag()

    def test_enabled_for_full_truck(self):
        self._set_flag("true")
        try:
            msg = _maybe_append_quote("Hi.", "full truck whole house")
            assert "$699-$699" in msg
        finally:
            self._clear_flag()

    def test_message_preserved_verbatim_when_appending(self):
        self._set_flag("true")
        try:
            original = "Original outreach text — keep me intact."
            msg = _maybe_append_quote(original, "few items")
            assert msg.startswith(original + "\n\n")
        finally:
            self._clear_flag()
