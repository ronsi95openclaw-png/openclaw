import json
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes import health
from hermes.briefing import compose_briefing, _alerts


# ── Helpers ───────────────────────────────────────────────────────────────────

def _touch(path: Path, content: str = "", age_seconds: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if age_seconds:
        mtime = time.time() - age_seconds
        os.utime(path, (mtime, mtime))
    return path


# ── ClawBot health ────────────────────────────────────────────────────────────

class TestClawbotHealth:
    def test_missing_data_dir_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            h = health.get_clawbot_health(data_dir=Path(d) / "nope")
            assert h["name"] == "ClawBot"
            assert h["running"] is False
            assert h["status"] == "unknown"
            assert h["last_seen"] == "never"
            assert h["recent_trades"] == []
            assert h["tjr_setups"] == []

    def test_fresh_conversation_history_is_running(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "conversation_history.json", "{}", age_seconds=10)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert h["running"] is True
            assert h["status"] == "running"
            assert "ago" in h["last_seen"]

    def test_stale_history_is_idle(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "conversation_history.json", "{}", age_seconds=7200)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert h["running"] is False
            assert h["status"] == "idle"

    def test_falls_back_to_usage_stats(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "usage_stats.json", "{}", age_seconds=30)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert h["running"] is True

    def test_recent_trades_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            line = ('TRADE_DECISION | 2026-06-18T08:00:00+00:00 | '
                    '{"action": "BUY", "coin": "BTC_USDT", "status": "executed"}\n')
            _touch(Path(d) / "logs" / "trades.log", line, age_seconds=10)
            _touch(Path(d) / "conversation_history.json", "{}", age_seconds=10)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert len(h["recent_trades"]) == 1
            assert h["recent_trades"][0]["action"] == "BUY"
            assert h["recent_trades"][0]["coin"] == "BTC_USDT"

    def test_tjr_setups_parsed_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            jl = '{"setup": "long", "coin": "ETH"}\n{"setup": "short", "coin": "SOL"}\n'
            _touch(Path(d) / "logs" / "tjr_setups.jsonl", jl)
            _touch(Path(d) / "conversation_history.json", "{}", age_seconds=10)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert len(h["tjr_setups"]) == 2

    def test_corrupt_trades_log_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "logs" / "trades.log", "TRADE_DECISION | bad | not json {{{")
            _touch(Path(d) / "conversation_history.json", "{}", age_seconds=10)
            h = health.get_clawbot_health(data_dir=Path(d))
            assert isinstance(h["recent_trades"], list)


# ── HaulYeah health ───────────────────────────────────────────────────────────

class TestHaulyeahHealth:
    def test_missing_data_dir_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            h = health.get_haulyeah_health(data_dir=Path(d) / "nope")
            assert h["name"] == "HaulYeah"
            assert h["running"] is False
            assert h["status"] == "unknown"
            assert h["pending_outreach"] == 0
            assert h["leads"] == 0

    def test_fresh_audit_is_running(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "audit.log", "{}\n", age_seconds=10)
            h = health.get_haulyeah_health(data_dir=Path(d))
            assert h["running"] is True
            assert h["status"] == "running"

    def test_pending_outreach_list_counted(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "audit.log", "{}\n", age_seconds=10)
            _touch(Path(d) / "pending_outreach.json", json.dumps([{"id": 1}, {"id": 2}]))
            h = health.get_haulyeah_health(data_dir=Path(d))
            assert h["pending_outreach"] == 2

    def test_pending_outreach_wrapped_dict_counted(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "audit.log", "{}\n", age_seconds=10)
            _touch(Path(d) / "pending_outreach.json",
                   json.dumps({"pending": [{"id": 1}, {"id": 2}, {"id": 3}]}))
            h = health.get_haulyeah_health(data_dir=Path(d))
            assert h["pending_outreach"] == 3

    def test_leads_store_counted(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "audit.log", "{}\n", age_seconds=10)
            _touch(Path(d) / "leads.json", json.dumps([{"name": "a"}]))
            h = health.get_haulyeah_health(data_dir=Path(d))
            assert h["leads"] == 1

    def test_corrupt_pending_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(Path(d) / "audit.log", "{}\n", age_seconds=10)
            _touch(Path(d) / "pending_outreach.json", "not json {{{")
            h = health.get_haulyeah_health(data_dir=Path(d))
            assert h["pending_outreach"] == 0


# ── Aggregate ─────────────────────────────────────────────────────────────────

class TestGetAllHealth:
    def test_never_raises_and_has_both_bots(self):
        out = health.get_all_health()
        assert "clawbot" in out
        assert "haulyeah" in out
        assert out["clawbot"]["name"] == "ClawBot"
        assert out["haulyeah"]["name"] == "HaulYeah"


# ── Briefing formatting ───────────────────────────────────────────────────────

class TestBriefing:
    def _health(self, **overrides):
        h = {
            "clawbot": {"name": "ClawBot", "running": True, "status": "running",
                        "last_seen": "10s ago", "age_seconds": 10,
                        "ollama": {"online": True}, "recent_trades": [], "tjr_setups": []},
            "haulyeah": {"name": "HaulYeah", "running": False, "status": "idle",
                         "last_seen": "30m ago", "age_seconds": 1800,
                         "pending_outreach": 0, "leads": 0},
        }
        h.update(overrides)
        return h

    def test_has_header_and_status_lines(self):
        msg = compose_briefing(self._health(), now="2026-06-18 08:00 UTC")
        assert "Hermes Briefing — 2026-06-18 08:00 UTC" in msg
        assert "ClawBot:" in msg
        assert "HaulYeah:" in msg

    def test_no_alerts_when_nominal(self):
        msg = compose_briefing(self._health(), now="t")
        assert "No alerts" in msg

    def test_idle_over_6h_alert(self):
        h = self._health()
        h["clawbot"]["running"] = False
        h["clawbot"]["status"] = "idle"
        h["clawbot"]["age_seconds"] = 7 * 3600
        msg = compose_briefing(h, now="t")
        assert "ClawBot idle >6h" in msg

    def test_pending_outreach_alert(self):
        h = self._health()
        h["haulyeah"]["pending_outreach"] = 4
        alerts = _alerts(h)
        assert any("4 new HaulYeah lead" in a for a in alerts)

    def test_tjr_setup_alert(self):
        h = self._health()
        h["clawbot"]["tjr_setups"] = [{"setup": "long"}]
        alerts = _alerts(h)
        assert any("TJR setup sent" in a for a in alerts)

    def test_unknown_clawbot_alert(self):
        h = self._health()
        h["clawbot"]["status"] = "unknown"
        h["clawbot"]["age_seconds"] = None
        alerts = _alerts(h)
        assert any("ClawBot status unknown" in a for a in alerts)
