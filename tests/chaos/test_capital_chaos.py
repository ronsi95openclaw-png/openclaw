"""Chaos tests: capital state machine under concurrent load and edge conditions."""
from __future__ import annotations

import threading
import time

import pytest


class TestCapitalStateChaos:
    def _make_engine(self, peak: float = 10000.0):
        from risk.capital_preservation import CapitalPreservationEngine
        return CapitalPreservationEngine(starting_equity=peak)

    def test_concurrent_updates_no_crash(self):
        """100 concurrent equity updates must not crash or corrupt state."""
        eng = self._make_engine(10000.0)
        errors = []

        def _update(equity):
            try:
                eng.update(equity)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_update, args=(10000 - i * 10,))
                   for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent update errors: {errors}"
        # State must be a valid enum value
        from risk.capital_preservation import CapitalState
        assert eng.get_state() in list(CapitalState)

    def test_drawdown_triggers_halt(self):
        """25%+ drawdown from peak must reach EMERGENCY_HALT."""
        from risk.capital_preservation import CapitalState
        eng = self._make_engine(10000.0)
        eng.update(10000.0)   # seed window
        eng.update(7400.0)    # 26% drawdown
        assert eng.get_state() == CapitalState.EMERGENCY_HALT

    def test_state_never_upgrades_on_single_update(self):
        """After CRITICAL, a single good equity reading should not jump back to SAFE."""
        from risk.capital_preservation import CapitalState
        eng = self._make_engine(10000.0)
        eng.update(10000.0)
        eng.update(8500.0)   # push into DEFENSIVE or CRITICAL
        state_after_drop = eng.get_state()
        if state_after_drop in (CapitalState.DEFENSIVE, CapitalState.CRITICAL):
            eng.update(10000.0)  # single bounce should NOT jump to SAFE
            assert eng.get_state() != CapitalState.SAFE

    def test_loss_streak_progression(self):
        """5 consecutive losses should escalate state."""
        from risk.capital_preservation import CapitalState
        eng = self._make_engine(10000.0)
        eng.update(10000.0)
        for _ in range(5):
            eng.update(10000.0, trade_pnl=-100.0)
        assert eng.get_state() != CapitalState.SAFE

    def test_persist_and_reload(self, tmp_path):
        """State persisted to disk must be restored on next init."""
        from risk import capital_preservation as cp_mod
        orig = cp_mod._STATE_FILE
        cp_mod._STATE_FILE = tmp_path / "cap_state.json"
        try:
            eng1 = cp_mod.CapitalPreservationEngine(starting_equity=10000.0)
            eng1.update(10000.0)
            eng1.update(8500.0)   # push into DEFENSIVE
            state_before = eng1.get_state()

            eng2 = cp_mod.CapitalPreservationEngine(starting_equity=10000.0)
            assert eng2.get_state() == state_before
        finally:
            cp_mod._STATE_FILE = orig

    def test_zero_equity_goes_to_halt(self):
        from risk.capital_preservation import CapitalState
        eng = self._make_engine(10000.0)
        eng.update(10000.0)
        eng.update(0.01)
        assert eng.get_state() == CapitalState.EMERGENCY_HALT


class TestReplayValidatorChaos:
    def test_empty_journal_passes(self, tmp_path):
        from runtime.replay_validator import ReplayValidator
        f = tmp_path / "replay.jsonl"
        f.write_text("")
        report = ReplayValidator.validate_file(str(f))
        assert report.passed
        assert report.total_events == 0

    def test_missing_file_handled(self):
        from runtime.replay_validator import ReplayValidator
        report = ReplayValidator.validate_file("/nonexistent/path.jsonl")
        assert not report.passed
        assert any("not found" in i.description.lower() for i in report.issues)

    def test_corrupt_json_line_flagged(self, tmp_path):
        from runtime.replay_validator import ReplayValidator
        f = tmp_path / "replay.jsonl"
        f.write_text('{"event_type":"signal_generated","ts":1000,"trace_id":"a"}\n'
                     'NOT VALID JSON\n')
        report = ReplayValidator.validate_file(str(f))
        assert any(i.severity == "ERROR" for i in report.issues)

    def test_time_backwards_flagged(self, tmp_path):
        from runtime.replay_validator import ReplayValidator
        import json
        events = [
            {"event_type": "signal_generated", "ts": 2000, "trace_id": "a"},
            {"event_type": "signal_generated", "ts": 1000, "trace_id": "b"},
        ]
        f = tmp_path / "replay.jsonl"
        f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        report = ReplayValidator.validate_file(str(f))
        assert any("backwards" in i.description.lower() or "order" in i.description.lower()
                   for i in report.issues)

    def test_illegal_capital_transition_flagged(self, tmp_path):
        from runtime.replay_validator import ReplayValidator
        import json
        # EMERGENCY_HALT → SAFE is genuinely illegal (no recovery path)
        events = [
            {"event_type": "capital_state_change", "ts": 1700000000000, "trace_id": "c",
             "payload": {"old_state": "EMERGENCY_HALT", "new_state": "SAFE"}},
        ]
        f = tmp_path / "replay.jsonl"
        f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        report = ReplayValidator.validate_file(str(f))
        error_descs = [i.description for i in report.issues if i.severity == "ERROR"]
        assert any("transition" in d.lower() or "illegal" in d.lower()
                   for d in error_descs)


class TestShadowOptimizationChaos:
    def test_large_weight_jump_rejected(self):
        from runtime.shadow_optimization import ShadowOptimizationEngine
        eng = ShadowOptimizationEngine()
        # Force snapshot to 0.40 so a jump to 0.71+ is definitely > 0.30 delta
        eng._snapshot["EMA_CROSS"] = 0.40
        outcomes = [True] * 18 + [False] * 2   # 90% win → confidence > 0.65
        eng.apply_candidate("EMA_CROSS", 0.75, "test",
                            actual_trades=20, raw_outcomes=outcomes)
        ok, reason = eng.promote("EMA_CROSS")
        assert not ok, f"Expected rejection for 0.35 jump, got approval (reason: {reason})"

    def test_low_trades_rejected(self):
        from runtime.shadow_optimization import ShadowOptimizationEngine
        eng = ShadowOptimizationEngine()
        eng.apply_candidate("EMA_CROSS", 0.6, "test", actual_trades=3)
        ok, reason = eng.promote("EMA_CROSS")
        assert not ok
        assert "trade" in reason.lower() or "sample" in reason.lower() or "minimum" in reason.lower()

    def test_rollback_restores_snapshot(self):
        from runtime.shadow_optimization import ShadowOptimizationEngine
        eng = ShadowOptimizationEngine()
        orig = eng._snapshot.get("EMA_CROSS", 0.5)
        eng.apply_candidate("EMA_CROSS", orig + 0.1, "test",
                            actual_trades=15, raw_outcomes=[True]*10+[False]*5)
        eng.rollback("EMA_CROSS")
        # After rollback the snapshot weight is preserved in the engine
        assert abs(eng._snapshot.get("EMA_CROSS", orig) - orig) < 0.01

    def test_concurrent_candidates_safe(self):
        from runtime.shadow_optimization import ShadowOptimizationEngine
        eng = ShadowOptimizationEngine()
        errors = []

        def _apply(name, w):
            try:
                eng.apply_candidate(name, w, "concurrent", actual_trades=15)
            except Exception as e:
                errors.append(e)

        strategies = ["EMA_CROSS", "RSI_MEAN_REVERT", "BREAKOUT", "BOLLINGER", "TREND_FOLLOW"]
        threads = [threading.Thread(target=_apply, args=(s, 0.55)) for s in strategies]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
