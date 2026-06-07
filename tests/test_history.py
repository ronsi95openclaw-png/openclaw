import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.history import format_report, load_trades, record_trade, summarize


def _trade(action="BUY", coin="BTC_USDT", usd=15.0):
    return {"action": action, "coin": coin, "usd_amount": usd, "status": "executed"}


class TestRecordAndLoad:
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.json")
            record_trade(_trade(), path=path)
            trades = load_trades(path=path)
            assert len(trades) == 1
            assert trades[0]["coin"] == "BTC_USDT"

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            assert load_trades(path=os.path.join(d, "nope.json")) == []

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.json")
            with open(path, "w") as f:
                f.write("not json {{{")
            assert load_trades(path=path) == []

    def test_appends_multiple(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.json")
            record_trade(_trade(action="BUY"), path=path)
            record_trade(_trade(action="SELL"), path=path)
            assert len(load_trades(path=path)) == 2

    def test_adds_recorded_at_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.json")
            record_trade(_trade(), path=path)
            assert "recorded_at" in load_trades(path=path)[0]

    def test_preserves_existing_recorded_at(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trades.json")
            entry = _trade()
            entry["recorded_at"] = "2026-01-01T00:00:00+00:00"
            record_trade(entry, path=path)
            assert load_trades(path=path)[0]["recorded_at"] == "2026-01-01T00:00:00+00:00"


class TestSummarize:
    def test_empty(self):
        s = summarize([])
        assert s["total"] == 0
        assert s["total_usd"] == 0
        assert s["by_action"] == {}
        assert s["recent"] == []

    def test_counts_by_action(self):
        s = summarize([_trade("BUY"), _trade("BUY"), _trade("SELL")])
        assert s["by_action"] == {"BUY": 2, "SELL": 1}

    def test_counts_by_coin(self):
        s = summarize([_trade(coin="BTC_USDT"), _trade(coin="ETH_USDT"), _trade(coin="BTC_USDT")])
        assert s["by_coin"]["BTC_USDT"] == 2
        assert s["by_coin"]["ETH_USDT"] == 1

    def test_total_usd(self):
        s = summarize([_trade(usd=10.0), _trade(usd=15.5)])
        assert s["total_usd"] == 25.5

    def test_recent_limited_to_five(self):
        s = summarize([_trade() for _ in range(8)])
        assert len(s["recent"]) == 5


class TestFormatReport:
    def test_empty_message(self):
        msg = format_report(summarize([]))
        assert "No executed trades" in msg

    def test_includes_total_and_count(self):
        msg = format_report(summarize([_trade("BUY"), _trade("SELL")]))
        assert "Total trades" in msg
        assert "2" in msg
