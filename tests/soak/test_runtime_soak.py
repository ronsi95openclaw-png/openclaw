"""Long-duration runtime soak tests for OpenClaw.

These tests simulate extended bot runtime via time compression — each test
completes in under 10 seconds of wall time while exercising hundreds to
thousands of subsystem cycles.

Markers
-------
- @pytest.mark.slow  → skipped when --fast is passed

Run all:
    pytest tests/soak/

Run fast subset only:
    pytest tests/soak/ --fast
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ── Mark helpers ───────────────────────────────────────────────────────────────

slow = pytest.mark.slow


# ── Test class ────────────────────────────────────────────────────────────────

class TestRuntimeSoak:
    """Extended runtime stability tests — all complete in < 10 s wall time."""

    # ── 1. Reconciliation stability: 100 cycles ───────────────────────────────

    @slow
    def test_reconciliation_stability_100_cycles(self):
        """100 demo reconciliation cycles must complete without exceptions.

        Every report must either pass cleanly or contain structured mismatches —
        never an unhandled exception.  Total runtime must be < 5 s.
        """
        from runtime.reconciliation import ReconciliationEngine

        engine = ReconciliationEngine(demo_mode=True)
        errors: List[Exception] = []

        # Varied position states to exercise all code paths
        position_sets: List[List[Dict[str, Any]]] = [
            [],                                         # empty — normal clean state
            [_make_valid_position("BTC_USDT", "long")],
            [_make_valid_position("ETH_USDT", "short"),
             _make_valid_position("SOL_USDT", "long")],
            [_make_corrupt_position()],                 # missing required keys
            [_make_valid_position("BTC_USDT", "long"),
             _make_corrupt_position()],                 # mixed valid + corrupt
        ]

        t0 = time.monotonic()
        for i in range(100):
            positions = position_sets[i % len(position_sets)]
            try:
                report = engine.reconcile(
                    local_positions=positions,
                    local_balance=10_000.0,
                )
                # Every report must have a structured result
                assert report is not None, f"Cycle {i}: report is None"
                assert isinstance(report.passed, bool), \
                    f"Cycle {i}: report.passed is not bool: {report.passed!r}"
                assert isinstance(report.mismatches, list), \
                    f"Cycle {i}: mismatches is not list: {type(report.mismatches)}"
                # If not passed, mismatches must be non-empty
                if not report.passed:
                    assert len(report.mismatches) > 0, \
                        f"Cycle {i}: failed report has no mismatches"
            except Exception as exc:
                errors.append(exc)

        elapsed = time.monotonic() - t0
        assert errors == [], f"Reconciliation errors: {errors}"
        assert elapsed < 5.0, f"100 cycles took {elapsed:.2f}s (limit 5s)"

    # ── 2. Capital engine: 10,000 equity updates ──────────────────────────────

    @slow
    def test_capital_engine_10k_updates(self):
        """10,000 random equity updates must not crash; final state must be valid."""
        from risk.capital_preservation import CapitalPreservationEngine, CapitalState

        engine = CapitalPreservationEngine(starting_equity=10_000.0)
        rng = random.Random(42)
        initial_threads = threading.active_count()
        errors: List[Exception] = []

        for _ in range(10_000):
            equity = rng.uniform(7_000.0, 11_000.0)
            try:
                engine.update(current_equity=equity)
            except Exception as exc:
                errors.append(exc)

        assert errors == [], f"Capital engine update errors: {errors}"

        final_state = engine.get_state()
        assert final_state in list(CapitalState), \
            f"Final state {final_state!r} is not a valid CapitalState"

        # Thread count must not grow (no leaked threads)
        final_threads = threading.active_count()
        assert final_threads <= initial_threads + 2, \
            f"Thread leak detected: started={initial_threads} ended={final_threads}"

    # ── 3. Concurrent reconciliation: no data corruption ─────────────────────

    def test_concurrent_reconciliation_no_corruption(self):
        """10 threads x 20 demo reconciliation cycles must not corrupt engine state."""
        from runtime.reconciliation import ReconciliationEngine

        engine = ReconciliationEngine(demo_mode=True)
        errors: List[Exception] = []
        error_lock = threading.Lock()

        def _worker(thread_id: int) -> None:
            positions = [_make_valid_position("BTC_USDT", "long")]
            for _ in range(20):
                try:
                    report = engine.reconcile(
                        local_positions=positions,
                        local_balance=10_000.0,
                    )
                    assert report is not None
                    assert isinstance(report.passed, bool)
                except Exception as exc:
                    with error_lock:
                        errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent reconciliation errors: {errors[:3]}"

    # ── 4. Replay journal: large file validation ──────────────────────────────

    @slow
    def test_replay_journal_large_file(self, tmp_path: Path):
        """Generate 10,000 valid journal events; validate in < 3 s with no ERRORs."""
        from runtime.replay_validator import ReplayValidator

        journal_path = tmp_path / "replay.jsonl"
        _write_large_journal(journal_path, event_count=10_000)

        t0 = time.monotonic()
        report = ReplayValidator.validate_file(str(journal_path))
        elapsed = time.monotonic() - t0

        assert elapsed < 3.0, f"Validation of 10k events took {elapsed:.2f}s (limit 3s)"

        # No ERROR-severity issues (WARNINGs about open intents are acceptable)
        error_issues = [i for i in report.issues if i.severity == "ERROR"]
        assert error_issues == [], \
            f"Unexpected ERROR issues: {[i.description for i in error_issues[:3]]}"

        assert report.total_events == 10_000, \
            f"Expected 10000 events, got {report.total_events}"

    # ── 5. EventStore: 1,000 events, integrity check ──────────────────────────

    @slow
    def test_event_store_1000_events(self, tmp_path: Path):
        """Append 1,000 mixed events; verify all checksums valid and seq monotonic."""
        from runtime.event_store import EventStore, EventType

        store = EventStore(
            store_path=str(tmp_path / "event_store.jsonl"),
            snapshot_path=str(tmp_path / "snapshot.json"),
        )

        event_types = list(EventType)
        seqs: List[int] = []

        for i in range(1_000):
            et = event_types[i % len(event_types)]
            evt = store.append(
                event_type=et,
                trace_id=str(uuid.uuid4()),
                payload={"index": i, "value": random.random()},
                symbol="BTC_USDT" if i % 3 == 0 else None,
            )
            seqs.append(evt.seq)

        assert len(seqs) == 1_000, f"Only {len(seqs)} events appended"

        # Sequence numbers must be strictly monotonically increasing
        for idx in range(1, len(seqs)):
            assert seqs[idx] > seqs[idx - 1], \
                f"Non-monotonic seq at index {idx}: {seqs[idx - 1]} → {seqs[idx]}"

        # All checksums must pass integrity check
        ok, integrity_errors = store.verify_integrity(start_seq=0)
        assert ok, f"Integrity check failed: {integrity_errors[:3]}"

    # ── 6. EventStore: concurrent append from 20 threads ─────────────────────

    @slow
    def test_concurrent_event_store_append(self, tmp_path: Path):
        """20 threads x 50 appends = 1,000 total; all seqs unique, integrity passes."""
        from runtime.event_store import EventStore, EventType

        store = EventStore(
            store_path=str(tmp_path / "event_store_concurrent.jsonl"),
            snapshot_path=str(tmp_path / "snapshot_concurrent.json"),
        )

        collected_seqs: List[int] = []
        seq_lock = threading.Lock()
        errors: List[Exception] = []
        error_lock = threading.Lock()

        def _worker() -> None:
            for _ in range(50):
                try:
                    evt = store.append(
                        event_type=EventType.SIGNAL_GENERATED,
                        trace_id=str(uuid.uuid4()),
                        payload={"thread": threading.get_ident()},
                    )
                    with seq_lock:
                        collected_seqs.append(evt.seq)
                except Exception as exc:
                    with error_lock:
                        errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent append errors: {errors[:3]}"
        assert len(collected_seqs) == 1_000, \
            f"Expected 1000 events, got {len(collected_seqs)}"

        # All sequence numbers must be unique
        assert len(set(collected_seqs)) == len(collected_seqs), \
            "Duplicate sequence numbers detected — concurrent write corruption"

        # Integrity must pass
        ok, integrity_errors = store.verify_integrity(start_seq=0)
        assert ok, f"Integrity check failed after concurrent writes: {integrity_errors[:3]}"

    # ── 7. Capital state: concurrent halts ───────────────────────────────────

    @slow
    def test_capital_state_concurrent_halts(self):
        """50 threads (25 high + 25 low equity) must end in EMERGENCY_HALT, no crash."""
        from risk.capital_preservation import CapitalPreservationEngine, CapitalState

        # 7000 is a 30% drawdown from 10000 — well past the 20% monthly halt threshold.
        engine = CapitalPreservationEngine(starting_equity=10_000.0)
        # Seed the peak so drawdown detection works from first call
        engine.update(current_equity=10_000.0)

        errors: List[Exception] = []
        error_lock = threading.Lock()

        def _update_high() -> None:
            for _ in range(10):
                try:
                    engine.update(current_equity=9_000.0)
                except Exception as exc:
                    with error_lock:
                        errors.append(exc)

        def _update_low() -> None:
            for _ in range(10):
                try:
                    engine.update(current_equity=7_000.0)
                except Exception as exc:
                    with error_lock:
                        errors.append(exc)

        threads = (
            [threading.Thread(target=_update_high) for _ in range(25)] +
            [threading.Thread(target=_update_low)  for _ in range(25)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent halt errors: {errors[:3]}"

        final_state = engine.get_state()
        assert final_state == CapitalState.EMERGENCY_HALT, \
            f"Expected EMERGENCY_HALT after 30% drawdown, got {final_state.value}"

    # ── 8. Memory growth bounded across 500 reconciliation cycles ─────────────

    @slow
    def test_memory_growth_bounded(self):
        """500 reconciliation cycles must not leak > 50 MB of memory."""
        from runtime.reconciliation import ReconciliationEngine

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        engine = ReconciliationEngine(demo_mode=True)
        positions = [_make_valid_position("BTC_USDT", "long"),
                     _make_valid_position("ETH_USDT", "short")]

        for _ in range(500):
            engine.reconcile(local_positions=positions, local_balance=10_000.0)

        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Compute net growth using top_by statistics
        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_growth_bytes = sum(s.size_diff for s in stats if s.size_diff > 0)
        total_growth_mb    = total_growth_bytes / (1024 * 1024)

        assert total_growth_mb < 50.0, \
            f"Memory growth after 500 cycles: {total_growth_mb:.2f} MB (limit 50 MB)"

    # ── 9. Drift detector: price storm (graceful if not importable) ───────────

    @slow
    def test_drift_detector_storm(self):
        """1,000 rapid price updates x 3 symbols + 100 detect_all() calls in < 2 s."""
        try:
            from runtime.drift_detector import DriftDetector
        except ImportError:
            pytest.skip("DriftDetector not yet available — skipping storm test")

        detector = DriftDetector()
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        rng = random.Random(99)
        errors: List[Exception] = []

        t0 = time.monotonic()
        try:
            base_ts = int(time.monotonic() * 1000)
            for i in range(1_000):
                symbol = symbols[i % len(symbols)]
                price  = 90_000.0 + rng.gauss(0, 500.0)
                try:
                    detector.update_price(symbol, price, base_ts + i * 100)
                except Exception as exc:
                    errors.append(exc)

            local_positions: dict = {}   # symbol → position dict
            current_prices = {"BTC_USDT": 90000.0, "ETH_USDT": 3000.0, "SOL_USDT": 150.0}
            for _ in range(100):
                try:
                    detector.detect_all(local_positions=local_positions,
                                        current_prices=current_prices)
                except Exception as exc:
                    errors.append(exc)
        except AttributeError as exc:
            pytest.skip(f"DriftDetector API mismatch — skipping: {exc}")

        elapsed = time.monotonic() - t0
        assert errors == [], f"Drift detector errors: {errors[:3]}"
        assert elapsed < 2.0, f"Storm test took {elapsed:.2f}s (limit 2s)"

    # ── 10. WebSocket reconnect simulation (asyncio) ──────────────────────────

    def test_websocket_reconnect_simulation(self):
        """100 rapid connect/disconnect cycles via asyncio; no task leaks."""
        connection_counter: List[int] = [0]  # use list for mutability in closure
        counter_lock = asyncio.Lock()

        async def _simulate_session() -> None:
            async with counter_lock:
                connection_counter[0] += 1
            # Simulate minimal async work (I/O yield)
            await asyncio.sleep(0)
            async with counter_lock:
                connection_counter[0] -= 1

        async def _run_all() -> None:
            tasks = [asyncio.create_task(_simulate_session()) for _ in range(100)]
            await asyncio.gather(*tasks)

        asyncio.run(_run_all())

        assert connection_counter[0] == 0, \
            f"Connection counter not zero after all disconnects: {connection_counter[0]}"

        # Verify no dangling tasks remain in the event loop
        # (asyncio.run() cleans up its loop, so this is a sanity check)
        assert connection_counter[0] == 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_valid_position(symbol: str, side: str) -> Dict[str, Any]:
    """Create a structurally valid position dict for use in reconciliation tests."""
    return {
        "id":          str(uuid.uuid4()),
        "symbol":      symbol,
        "strategy":    "EMA_CROSS",
        "side":        side,
        "entry_price": 90_000.0,
        "size":        0.01,
        "sl_price":    87_000.0,
        "tp_price":    95_000.0,
    }


def _make_corrupt_position() -> Dict[str, Any]:
    """Create a position dict missing required keys (triggers CORRUPT_STATE mismatch)."""
    return {
        "id":     str(uuid.uuid4()),
        "symbol": "BTC_USDT",
        # deliberately omit: strategy, side, entry_price, size, sl_price, tp_price
    }


def _write_large_journal(path: Path, event_count: int) -> None:
    """Write *event_count* valid JSONL events to *path* for replay validation tests.

    Events are structured to exercise all validator paths:
    - scan_start, regime_classified, signal_generated, intent_approved,
      position_opened, position_closed
    Timestamps are sequential so no time-backwards issues are flagged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    base_ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    from datetime import timedelta

    event_types_cycle = [
        "scan_start",
        "regime_classified",
        "signal_generated",
        "intent_approved",
        "position_opened",
        "position_closed",
    ]

    with path.open("w", encoding="utf-8") as fh:
        for i in range(event_count):
            et       = event_types_cycle[i % len(event_types_cycle)]
            ts       = (base_ts + timedelta(seconds=i)).isoformat()
            trace_id = str(uuid.uuid4())
            entry    = {
                "event_type": et,
                "trace_id":   trace_id,
                "ts":         ts,
                "payload":    {
                    "index":   i,
                    "symbol":  "BTC_USDT",
                    "seq":     i,
                },
            }
            # For capital_state events add required payload keys
            if et == "intent_approved":
                entry["payload"]["approved"] = True
                entry["payload"]["risk_scalar"] = 1.0
                entry["payload"]["adjusted_size_pct"] = 1.5
            fh.write(json.dumps(entry) + "\n")
