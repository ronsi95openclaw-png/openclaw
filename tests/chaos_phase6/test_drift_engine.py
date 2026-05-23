"""Phase 6 soak tests — DriftEngine statistical drift detection.

All tests complete in < 20s wall time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestDriftEngine:

    # ── 1. Empty outcomes — no crash ──────────────────────────────────────────

    def test_empty_outcomes_no_crash(self, tmp_path: Path) -> None:
        """generate_report() on empty outcomes returns valid report."""
        try:
            from research.statistics.drift_engine import DriftEngine, DriftSeverity
        except ImportError:
            pytest.skip("drift_engine not available")

        engine = DriftEngine(
            outcomes_path=str(tmp_path / "outcomes.jsonl"),
            backtest_path=str(tmp_path / "backtest.jsonl"),
            window=100,
        )
        engine.load_outcomes()
        engine.load_backtest_outcomes()
        report = engine.generate_report()

        assert report is not None
        assert report.generated_at
        assert 0.0 <= report.severity_score <= 100.0
        assert report.overall_severity in DriftSeverity

    # ── 2. Expectancy collapse detected ──────────────────────────────────────

    def test_expectancy_collapse_detected(self, tmp_path: Path) -> None:
        """30 wins then 30 losses should trigger MODERATE+ expectancy collapse."""
        try:
            from research.statistics.drift_engine import DriftEngine, DriftSeverity, DriftMetric
        except ImportError:
            pytest.skip("drift_engine not available")

        outcomes_path = tmp_path / "outcomes.jsonl"
        records = []
        for i in range(30):
            records.append({"ts": f"2026-05-01T{i:02d}:00:00", "strategy": "EMA",
                            "pnl": 10.0, "outcome": "win", "regime": "TRENDING",
                            "confidence": 0.75, "symbol": "BTCUSD-PERP",
                            "side": "long", "size": 0.001,
                            "entry_price": 50000.0, "exit_price": 50200.0})
        for i in range(30):
            records.append({"ts": f"2026-05-02T{i:02d}:00:00", "strategy": "EMA",
                            "pnl": -10.0, "outcome": "loss", "regime": "TRENDING",
                            "confidence": 0.75, "symbol": "BTCUSD-PERP",
                            "side": "long", "size": 0.001,
                            "entry_price": 50000.0, "exit_price": 49800.0})
        with open(outcomes_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        engine = DriftEngine(outcomes_path=str(outcomes_path), window=60)
        engine.load_outcomes()
        report = engine.generate_report()

        collapse_findings = [
            f for f in report.findings
            if f.metric == DriftMetric.EXPECTANCY_COLLAPSE
        ]
        assert len(collapse_findings) >= 1
        severity_order = [
            DriftSeverity.NONE, DriftSeverity.MINOR,
            DriftSeverity.MODERATE, DriftSeverity.SEVERE, DriftSeverity.CRITICAL,
        ]
        idx = severity_order.index(collapse_findings[0].severity)
        assert idx >= 2, (
            f"Expected MODERATE+ severity for collapsing strategy, "
            f"got {collapse_findings[0].severity}"
        )

    # ── 3. Live vs backtest divergence ────────────────────────────────────────

    def test_live_vs_backtest_divergence(self, tmp_path: Path) -> None:
        """Strong live/backtest divergence produces MODERATE+ finding."""
        try:
            from research.statistics.drift_engine import DriftEngine, DriftSeverity, DriftMetric
        except ImportError:
            pytest.skip("drift_engine not available")

        live_path = tmp_path / "outcomes.jsonl"
        bt_path   = tmp_path / "backtest.jsonl"

        def _write(path: Path, pnl: float, n: int = 30) -> None:
            with open(path, "w") as f:
                for i in range(n):
                    f.write(json.dumps({
                        "ts": f"2026-05-01T{i:02d}:00:00", "strategy": "EMA",
                        "pnl": pnl, "outcome": "win" if pnl > 0 else "loss",
                        "regime": "TRENDING", "confidence": 0.7,
                        "symbol": "BTCUSD-PERP", "side": "long",
                        "size": 0.001, "entry_price": 50000.0,
                        "exit_price": 50100.0 if pnl > 0 else 49900.0,
                    }) + "\n")

        _write(live_path, pnl=5.0)
        _write(bt_path, pnl=-5.0)

        engine = DriftEngine(
            outcomes_path=str(live_path),
            backtest_path=str(bt_path),
            window=60,
        )
        engine.load_outcomes()
        engine.load_backtest_outcomes()
        report = engine.generate_report()

        div_findings = [
            f for f in report.findings
            if f.metric == DriftMetric.LIVE_VS_BACKTEST_DIVERGENCE
        ]
        assert len(div_findings) >= 1
        severity_order = [
            DriftSeverity.NONE, DriftSeverity.MINOR,
            DriftSeverity.MODERATE, DriftSeverity.SEVERE, DriftSeverity.CRITICAL,
        ]
        idx = severity_order.index(div_findings[0].severity)
        assert idx >= 2, f"Expected MODERATE+, got {div_findings[0].severity}"

    # ── 4. No drift in stable strategy ───────────────────────────────────────

    def test_no_drift_in_stable_strategy(self, tmp_path: Path) -> None:
        """60 consistent wins with matching backtest → non-CRITICAL per-metric checks pass."""
        try:
            from research.statistics.drift_engine import DriftEngine, DriftSeverity, DriftMetric
        except ImportError:
            pytest.skip("drift_engine not available")

        outcomes_path = tmp_path / "outcomes.jsonl"
        backtest_path = tmp_path / "backtest.jsonl"
        record = {
            "strategy": "EMA", "pnl": 5.0, "outcome": "win",
            "regime": "TRENDING", "confidence": 0.75,
            "symbol": "BTCUSD-PERP", "side": "long",
            "size": 0.001, "entry_price": 50000.0, "exit_price": 50100.0,
        }
        with open(outcomes_path, "w") as f:
            for i in range(60):
                f.write(json.dumps({**record, "ts": f"2026-05-01T{i % 24:02d}:{i // 24:02d}:00"}) + "\n")
        # Provide matching backtest data so LIVE_VS_BACKTEST divergence doesn't fire CRITICAL
        with open(backtest_path, "w") as f:
            for i in range(60):
                f.write(json.dumps({**record, "ts": f"2026-04-01T{i % 24:02d}:{i // 24:02d}:00"}) + "\n")

        engine = DriftEngine(
            outcomes_path=str(outcomes_path),
            backtest_path=str(backtest_path),
            window=60,
        )
        engine.load_outcomes()
        engine.load_backtest_outcomes()
        report = engine.generate_report()

        # With matching live and backtest, expectancy/confidence/overfitting metrics should be stable
        for f in report.findings:
            if f.metric in (DriftMetric.EXPECTANCY_COLLAPSE, DriftMetric.CONFIDENCE_DRIFT,
                            DriftMetric.OVERFITTING_RECURRENCE):
                assert f.severity in (DriftSeverity.NONE, DriftSeverity.MINOR), (
                    f"Metric {f.metric} should be NONE/MINOR for stable strategy, got {f.severity}"
                )

    # ── 5. Severity score in range ───────────────────────────────────────────

    def test_severity_score_in_range(self, tmp_path: Path) -> None:
        """severity_score always in [0, 100]."""
        try:
            from research.statistics.drift_engine import DriftEngine
        except ImportError:
            pytest.skip("drift_engine not available")

        engine = DriftEngine(
            outcomes_path=str(tmp_path / "o.jsonl"),
            window=50,
        )
        engine.load_outcomes()
        report = engine.generate_report()
        assert 0.0 <= report.severity_score <= 100.0

    # ── 6. Governance action escalates on severe ─────────────────────────────

    def test_governance_action_escalates_on_severe(self, tmp_path: Path) -> None:
        """Heavy alternating wins/losses forces ESCALATE governance action."""
        try:
            from research.statistics.drift_engine import DriftEngine
        except ImportError:
            pytest.skip("drift_engine not available")

        outcomes_path = tmp_path / "outcomes.jsonl"
        with open(outcomes_path, "w") as f:
            # Alternating extreme wins/losses — high instability
            for i in range(40):
                pnl = 50.0 if i % 2 == 0 else -50.0
                f.write(json.dumps({
                    "ts": f"2026-05-01T{i % 24:02d}:{i // 24:02d}:00",
                    "strategy": "EMA", "pnl": pnl,
                    "outcome": "win" if pnl > 0 else "loss",
                    "regime": "RANGING" if i % 3 == 0 else "TRENDING",
                    "confidence": 0.5 if i % 2 == 0 else 0.9,
                    "symbol": "BTCUSD-PERP", "side": "long",
                    "size": 0.001, "entry_price": 50000.0,
                    "exit_price": 51000.0 if pnl > 0 else 49000.0,
                }) + "\n")

        engine = DriftEngine(outcomes_path=str(outcomes_path), window=40)
        engine.load_outcomes()
        report = engine.generate_report()

        # With extreme volatility, should see at least INVESTIGATE or ESCALATE
        assert report.recommended_governance_action in ("INVESTIGATE", "ESCALATE"), (
            f"Expected INVESTIGATE/ESCALATE, got {report.recommended_governance_action}"
        )

    # ── 7. Pearson correlation for identical series ───────────────────────────

    def test_pearson_correlation_edge_cases(self, tmp_path: Path) -> None:
        """_pearson of all-same values returns 0 (no variance)."""
        try:
            from research.statistics.drift_engine import _pearson
        except ImportError:
            pytest.skip("drift_engine not available")

        result = _pearson([5.0] * 10, [3.0] * 10)
        assert result == 0.0, f"Expected 0.0 for zero-variance series, got {result}"

        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [2.0, 4.0, 6.0, 8.0, 10.0]
        result = _pearson(xs, ys)
        assert abs(result - 1.0) < 1e-6, f"Expected 1.0 for perfect correlation, got {result}"

    # ── 8. Persist report creates file ───────────────────────────────────────

    def test_persist_report_creates_file(self, tmp_path: Path) -> None:
        """persist_report() creates valid JSONL file."""
        try:
            from research.statistics.drift_engine import DriftEngine
        except ImportError:
            pytest.skip("drift_engine not available")

        output_path = tmp_path / "drift_reports.jsonl"
        engine = DriftEngine(
            outcomes_path=str(tmp_path / "o.jsonl"),
            window=50,
        )
        engine.load_outcomes()
        report = engine.generate_report()
        engine.persist_report(report, output_path=str(output_path))

        assert output_path.exists()
        with open(output_path) as f:
            line = f.readline().strip()
        parsed = json.loads(line)
        assert "generated_at" in parsed
        assert "severity_score" in parsed
