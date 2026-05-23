"""Tests for runtime/scan_interval_engine.py — dynamic scan interval with debounce."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from runtime.scan_interval_engine import ScanIntervalEngine, IntervalTransition, get_scan_engine


class TestRegimeToInterval:
    def test_trending_bull_returns_15(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("TRENDING_BULL") == 15

    def test_trending_bear_returns_15(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("TRENDING_BEAR") == 15

    def test_momentum_bull_returns_15(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("MOMENTUM_BULL") == 15

    def test_news_spike_returns_15(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("NEWS_SPIKE") == 15

    def test_ranging_returns_60(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("RANGING") == 60

    def test_unknown_returns_60(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("UNKNOWN") == 60

    def test_liquidity_drought_returns_60(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("LIQUIDITY_DROUGHT") == 60

    def test_novel_regime_returns_60_fail_closed(self):
        eng = ScanIntervalEngine()
        assert eng._regime_to_interval("NEVER_SEEN_BEFORE") == 60

    def test_bounds_respected(self):
        eng = ScanIntervalEngine(min_interval=20, max_interval=45)
        assert eng._regime_to_interval("TRENDING_BULL") == 20
        assert eng._regime_to_interval("RANGING") == 45


class TestDebounce:
    def test_single_regime_reading_does_not_switch(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=2)
        # First call with trending — should NOT switch yet (count=1, need 2)
        result = eng.compute_interval("TRENDING_BULL", set())
        assert result == 60  # still old interval

    def test_two_consecutive_readings_switch(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=2)
        eng.compute_interval("TRENDING_BULL", set())  # count=1
        result = eng.compute_interval("TRENDING_BULL", set())  # count=2 → switch
        assert result == 15

    def test_inconsistent_regimes_reset_counter(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=2)
        eng.compute_interval("TRENDING_BULL", set())   # pending=15, count=1
        eng.compute_interval("RANGING", set())         # pending=60, count=1 (reset)
        result = eng.compute_interval("TRENDING_BULL", set())  # pending=15, count=1 (reset again)
        assert result == 60  # still at default, not switched

    def test_debounce_count_1_switches_immediately(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=1)
        result = eng.compute_interval("TRENDING_BULL", set())
        assert result == 15


class TestComputeInterval:
    def test_none_regime_with_no_positions_returns_default(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=1)
        result = eng.compute_interval(None, set())
        assert result == 60

    def test_none_regime_with_fast_position_returns_15(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=1)
        result = eng.compute_interval(None, {"TRENDING_BULL"})
        assert result == 15

    def test_none_regime_with_slow_position_returns_60(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=1)
        result = eng.compute_interval(None, {"RANGING"})
        assert result == 60

    def test_intent_regime_takes_priority_over_position(self):
        eng = ScanIntervalEngine(default_interval=60, debounce_count=1)
        # Intent says RANGING (slow) even though positions say TRENDING_BULL
        result = eng.compute_interval("RANGING", {"TRENDING_BULL"})
        assert result == 60

    def test_interval_always_in_bounds(self):
        eng = ScanIntervalEngine(min_interval=15, max_interval=60, debounce_count=1)
        for regime in ["TRENDING_BULL", "RANGING", "UNKNOWN", "NEWS_SPIKE", "LIQUIDITY_DROUGHT"]:
            val = eng.compute_interval(regime, set())
            assert 15 <= val <= 60, f"{regime} → {val} out of bounds"


class TestApply:
    def test_apply_writes_transition(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        eng = ScanIntervalEngine(audit_path=str(tmp_path / "data" / "scan_audit.jsonl"), debounce_count=1)
        eng.compute_interval("TRENDING_BULL", set())  # pending=15, count=1 → ready

        transition = eng.apply(15, 60, "TRENDING_BULL", "intent_pipeline")
        assert transition is not None
        assert transition.old_interval == 60
        assert transition.new_interval == 15
        assert transition.regime == "TRENDING_BULL"

    def test_apply_no_change_returns_none(self):
        eng = ScanIntervalEngine()
        result = eng.apply(60, 60, "UNKNOWN", "fallback")
        assert result is None

    def test_apply_writes_audit_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        audit_path = str(tmp_path / "data" / "scan_audit.jsonl")
        eng = ScanIntervalEngine(audit_path=audit_path, debounce_count=1)
        eng.compute_interval("TRENDING_BULL", set())
        eng.apply(15, 60, "TRENDING_BULL", "intent_pipeline")

        records = [json.loads(l) for l in Path(audit_path).read_text().splitlines() if l.strip()]
        assert len(records) == 1
        assert records[0]["new_interval"] == 15

    def test_apply_updates_current_interval(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        eng = ScanIntervalEngine(audit_path=str(tmp_path / "data" / "s.jsonl"), debounce_count=1)
        eng.apply(15, 60, "TRENDING_BULL", "intent_pipeline")
        assert eng.get_current_interval() == 15

    def test_audit_write_failure_doesnt_raise(self, monkeypatch):
        eng = ScanIntervalEngine(audit_path="/nonexistent_dir/scan_audit.jsonl")
        eng.apply(15, 60, "TRENDING_BULL", "intent_pipeline")  # must not raise


class TestConcurrency:
    def test_concurrent_compute_no_race(self):
        eng = ScanIntervalEngine(debounce_count=2)
        results = []
        errors = []

        def worker(regime):
            try:
                for _ in range(50):
                    val = eng.compute_interval(regime, set())
                    assert 15 <= val <= 60
                    results.append(val)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker, args=("TRENDING_BULL",)) for _ in range(5)]
        threads += [threading.Thread(target=worker, args=("RANGING",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent errors: {errors}"

    def test_get_status_returns_dict(self):
        eng = ScanIntervalEngine()
        status = eng.get_status()
        assert "current_interval" in status
        assert "min_interval" in status
        assert "max_interval" in status
        assert "debounce_count" in status


class TestSingleton:
    def test_get_scan_engine_returns_same_instance(self):
        a = get_scan_engine()
        b = get_scan_engine()
        assert a is b
