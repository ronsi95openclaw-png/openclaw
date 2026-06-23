import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from core import workspaces as ws


def _store(d):
    return Path(d) / "workspaces.json"


class TestTopicKey:
    def test_plain_chat_keys_on_chat_id(self):
        assert ws.topic_key(123) == "123"

    def test_topic_appends_thread(self):
        assert ws.topic_key(123, 7) == "123:7"

    def test_thread_zero_is_distinct_from_none(self):
        assert ws.topic_key(123, 0) == "123:0"
        assert ws.topic_key(123) == "123"


class TestResolve:
    def test_unbound_topic_defaults_to_general(self):
        with tempfile.TemporaryDirectory() as d:
            w = ws.resolve(1, 2, path=_store(d))
            assert w.key == "general"

    def test_missing_store_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            assert ws.resolve(1, path=_store(d)).key == "general"

    def test_corrupt_store_falls_back_to_general(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            p.write_text("not json {{{", encoding="utf-8")
            assert ws.resolve(1, 2, path=p).key == "general"


class TestBindTopic:
    def test_bind_changes_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            ws.bind_topic(1, 2, "trading", path=p)
            assert ws.resolve(1, 2, path=p).key == "trading"

    def test_bind_is_isolated_per_topic(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            ws.bind_topic(1, 2, "trading", path=p)
            ws.bind_topic(1, 3, "content", path=p)
            assert ws.resolve(1, 2, path=p).key == "trading"
            assert ws.resolve(1, 3, path=p).key == "content"
            # An unrelated topic is still general.
            assert ws.resolve(1, 9, path=p).key == "general"

    def test_unknown_workspace_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(ValueError):
                ws.bind_topic(1, 2, "nope", path=_store(d))

    def test_bind_persists_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            ws.bind_topic(1, 2, "admin", path=p)
            assert p.exists()
            assert ws.resolve(1, 2, path=p).key == "admin"


class TestRules:
    def test_custom_rule_overrides_template_rule(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            ws.bind_topic(1, 2, "trading", path=p)
            w = ws.set_rule(1, 2, "Only talk about BTC.", path=p)
            assert w.rule == "Only talk about BTC."
            # Identity (name/emoji) is preserved from the template.
            assert w.key == "trading"
            assert w.name == "Trading"

    def test_empty_rule_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with pytest.raises(ValueError):
                ws.set_rule(1, 2, "   ", path=_store(d))

    def test_clear_rule_reverts_to_template(self):
        with tempfile.TemporaryDirectory() as d:
            p = _store(d)
            ws.bind_topic(1, 2, "trading", path=p)
            ws.set_rule(1, 2, "custom", path=p)
            reverted = ws.clear_rule(1, 2, path=p)
            template = next(w for w in ws.list_workspaces() if w.key == "trading")
            assert reverted.rule == template.rule

    def test_clear_rule_on_unset_topic_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            w = ws.clear_rule(1, 2, path=_store(d))
            assert w.key == "general"


class TestSystemPrompt:
    def test_layers_rule_on_base(self):
        base = "You are ClawBot."
        w = next(x for x in ws.list_workspaces() if x.key == "content")
        prompt = ws.system_prompt(base, w)
        assert prompt.startswith("You are ClawBot.")
        assert w.rule in prompt
        assert "Active workspace" in prompt
        assert w.name in prompt


class TestListWorkspaces:
    def test_includes_recommended_rooms(self):
        keys = {w.key for w in ws.list_workspaces()}
        assert {"general", "trading", "content", "admin"} <= keys

    def test_label_combines_emoji_and_name(self):
        w = next(x for x in ws.list_workspaces() if x.key == "trading")
        assert w.emoji in w.label
        assert w.name in w.label
