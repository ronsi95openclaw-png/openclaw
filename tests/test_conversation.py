import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from core import conversation as conv


@pytest.fixture
def temp_store(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(conv, "_HISTORY_FILE", Path(d) / "history.json")
        yield


class TestTopicIsolation:
    def test_threads_have_separate_history(self, temp_store):
        conv.add_message(1, "user", "trading talk", thread_id=2)
        conv.add_message(1, "user", "content talk", thread_id=3)
        assert conv.get_history(1, 2) == [{"role": "user", "content": "trading talk"}]
        assert conv.get_history(1, 3) == [{"role": "user", "content": "content talk"}]

    def test_plain_chat_separate_from_topic(self, temp_store):
        conv.add_message(1, "user", "plain")
        conv.add_message(1, "user", "topic", thread_id=2)
        assert conv.get_history(1) == [{"role": "user", "content": "plain"}]
        assert conv.get_history(1, 2) == [{"role": "user", "content": "topic"}]

    def test_clear_only_affects_one_topic(self, temp_store):
        conv.add_message(1, "user", "a", thread_id=2)
        conv.add_message(1, "user", "b", thread_id=3)
        conv.clear_history(1, 2)
        assert conv.get_history(1, 2) == []
        assert conv.get_history(1, 3) == [{"role": "user", "content": "b"}]


class TestBackwardCompatibility:
    def test_no_thread_round_trip(self, temp_store):
        conv.add_message(5, "user", "hi")
        conv.add_message(5, "assistant", "hello")
        assert conv.get_history(5) == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_caps_at_max_history(self, temp_store):
        for i in range(conv.MAX_HISTORY + 5):
            conv.add_message(7, "user", str(i), thread_id=1)
        assert len(conv.get_history(7, 1)) == conv.MAX_HISTORY
