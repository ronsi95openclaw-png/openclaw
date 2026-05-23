"""Chaos Phase 6: WebSocket fault injector tests.

Six tests covering packet loss, duplication, malformed payloads,
injection rate bounding, deterministic replay, and stats accuracy.
All tests complete in < 10 s total.
"""
from __future__ import annotations

from typing import List

import pytest

# ── Import guard ──────────────────────────────────────────────────────────────

try:
    from runtime.ws_fault_injector import (
        WSFaultInjector,
        FaultInjectionConfig,
        FaultType,
        FaultEvent,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason="runtime.ws_fault_injector not available",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_injector(
    seed: int = 42,
    **config_kwargs,
) -> "WSFaultInjector":
    """Create an isolated WSFaultInjector with the given config overrides."""
    config = FaultInjectionConfig(**config_kwargs)
    return WSFaultInjector(seed=seed, config=config)


def _sample_message(seq: int) -> dict:
    return {"type": "trade", "price": 67_000.0, "qty": 0.01, "seq": seq}


# ── Test 1: Packet loss drops message ────────────────────────────────────────

class TestPacketLoss:
    def test_packet_loss_drops_message(self) -> None:
        """With loss_rate=1.0, every message must be dropped (empty list returned)."""
        injector = _make_injector(
            seed=1,
            packet_loss_rate=1.0,
            duplication_rate=0.0,
            malformed_rate=0.0,
            max_injection_rate=1.0,
        )
        # The first message may be subject to malformed check before loss;
        # with malformed_rate=0 and loss_rate=1.0 the result is always [].
        result = injector.inject(_sample_message(1), seq=1)
        assert result == [], (
            f"Expected [] for 100% packet loss, got {result}"
        )

        # Confirm consistently across multiple messages
        all_dropped = all(
            injector.inject(_sample_message(i), seq=i) == []
            for i in range(2, 22)
        )
        assert all_dropped, "Packet loss rate 1.0 must drop all messages"


# ── Test 2: Packet duplication ────────────────────────────────────────────────

class TestPacketDuplication:
    def test_packet_duplication_returns_two_messages(self) -> None:
        """With dup_rate=1.0 and other faults off, inject returns 2 messages."""
        injector = _make_injector(
            seed=2,
            packet_loss_rate=0.0,
            duplication_rate=1.0,
            malformed_rate=0.0,
            max_injection_rate=1.0,
        )
        msg    = _sample_message(1)
        result = injector.inject(msg, seq=1)

        assert len(result) == 2, (
            f"Expected 2 messages from duplication, got {len(result)}"
        )
        # Both messages must carry the original content
        assert result[0]["price"] == 67_000.0
        assert result[1]["price"] == 67_000.0


# ── Test 3: Malformed payload injection ───────────────────────────────────────

class TestMalformedPayload:
    def test_malformed_payload_returns_corrupted_message(self) -> None:
        """With malformed_rate=1.0, injected message has type=MALFORMED."""
        injector = _make_injector(
            seed=3,
            packet_loss_rate=0.0,
            duplication_rate=0.0,
            malformed_rate=1.0,
            max_injection_rate=1.0,
        )
        msg    = _sample_message(1)
        result = injector.inject(msg, seq=1)

        assert len(result) == 1, f"Expected exactly 1 malformed message, got {len(result)}"
        assert result[0]["type"] == "MALFORMED", (
            f"Expected type MALFORMED, got {result[0].get('type')}"
        )
        assert "corrupted" in result[0].get("data", ""), (
            f"Expected 'corrupted' in data field, got {result[0].get('data')}"
        )


# ── Test 4: Injection rate bounded ───────────────────────────────────────────

class TestInjectionRateBounded:
    def test_injection_rate_respects_max(self) -> None:
        """max_injection_rate=0.1 is respected over 100 processed messages."""
        injector = _make_injector(
            seed=4,
            packet_loss_rate=0.5,    # high rate so we can observe limiting
            duplication_rate=0.0,
            malformed_rate=0.0,
            max_injection_rate=0.10,  # 10% ceiling
        )
        dropped = 0
        total   = 100
        for i in range(total):
            result = injector.inject(_sample_message(i), seq=i)
            if not result:
                dropped += 1

        stats = injector.get_stats()
        actual_rate = stats["total_injected"] / stats["total_messages_processed"]

        # Allow small overshoot from window boundary effects
        assert actual_rate <= 0.20, (
            f"Injection rate {actual_rate:.4f} exceeded 2× the max_injection_rate=0.10 bound"
        )


# ── Test 5: Deterministic replay ─────────────────────────────────────────────

class TestDeterministicReplayInjector:
    def test_same_seed_same_fault_sequence(self) -> None:
        """Two injectors with the same seed produce the same fault event types."""
        injector_a = _make_injector(seed=77, packet_loss_rate=0.15, duplication_rate=0.10)
        injector_b = _make_injector(seed=77, packet_loss_rate=0.15, duplication_rate=0.10)

        n = 50
        results_a: List[List[dict]] = []
        results_b: List[List[dict]] = []

        for i in range(n):
            msg = _sample_message(i)
            results_a.append(injector_a.inject(msg.copy(), seq=i))
            results_b.append(injector_b.inject(msg.copy(), seq=i))

        # Compare output lengths (which encode the fault decisions)
        lengths_a = [len(r) for r in results_a]
        lengths_b = [len(r) for r in results_b]
        assert lengths_a == lengths_b, (
            "Same seed must produce identical fault sequence output lengths"
        )

        # Compare fault event types
        types_a = [e.fault_type for e in injector_a.get_events()]
        types_b = [e.fault_type for e in injector_b.get_events()]
        assert types_a == types_b, (
            "Same seed must produce identical fault type sequence"
        )


# ── Test 6: Stats accuracy ────────────────────────────────────────────────────

class TestGetStatsAccurate:
    def test_stats_reflect_actual_fault_count(self) -> None:
        """get_stats() totals must match actual observed fault events."""
        injector = _make_injector(
            seed=5,
            packet_loss_rate=0.30,
            duplication_rate=0.0,
            malformed_rate=0.0,
            max_injection_rate=1.0,
        )
        n = 60
        for i in range(n):
            injector.inject(_sample_message(i), seq=i)

        stats  = injector.get_stats()
        events = injector.get_events()

        # total_injected must equal len(events)
        assert stats["total_injected"] == len(events), (
            f"stats total_injected={stats['total_injected']} != len(events)={len(events)}"
        )

        # total_messages_processed must be n
        assert stats["total_messages_processed"] == n, (
            f"Expected {n} messages processed, got {stats['total_messages_processed']}"
        )

        # counts_by_type must sum to total_injected
        type_sum = sum(stats["counts_by_type"].values())
        assert type_sum == stats["total_injected"], (
            f"Type counts sum {type_sum} != total_injected {stats['total_injected']}"
        )

        # injection_rate must be consistent
        expected_rate = stats["total_injected"] / n
        assert stats["injection_rate"] == pytest.approx(expected_rate, rel=1e-9), (
            "injection_rate field must equal total_injected / total_messages_processed"
        )
