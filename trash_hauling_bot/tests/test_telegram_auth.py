import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import config
from integrations.telegram_bot import _is_authorized


class TestTelegramAuth:
    def test_empty_allowlist_denies_all(self, monkeypatch):
        monkeypatch.setattr(config, "authorized_chat_ids", [])
        assert _is_authorized(12345) is False

    def test_populated_allowlist_still_works(self, monkeypatch):
        monkeypatch.setattr(config, "authorized_chat_ids", [123])
        assert _is_authorized(123) is True
        assert _is_authorized(456) is False
