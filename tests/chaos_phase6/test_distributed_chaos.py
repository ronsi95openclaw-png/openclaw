"""Distributed coordination chaos tests — Phase 6.

Tests validate that DistributedLock / LeaderElection maintain single-leader
guarantees, respect TTL expiry, and prevent split-brain across all adversarial
scenarios.

All tests complete in < 45s total.
"""
from __future__ import annotations

import time

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_dc(tmp_path):
    try:
        from runtime.distributed_chaos import DistributedChaos  # type: ignore[import]
        return DistributedChaos(
            seed     = 42,
            lock_dir = str(tmp_path / "chaos_locks"),
        )
    except ImportError as exc:
        pytest.skip(f"distributed_chaos unavailable: {exc}")


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDistributedChaos:
    """Distributed coordination failure simulations — all in < 45s."""

    def test_split_brain_prevention(self, tmp_path):
        """3 concurrent nodes — exactly 1 must acquire the lock.

        Verifies that DistributedLock prevents split-brain under concurrent
        acquisition attempts.
        """
        dc = _get_dc(tmp_path)
        result = dc.simulate_split_brain_attempt(node_count=3)

        assert result.single_leader_maintained is True, (
            f"Split-brain prevention failed: single_leader_maintained=False "
            f"details={result.incident_details}"
        )

    def test_stale_leader_recovery(self, tmp_path):
        """Stale lock (TTL=1s) must allow node-B to acquire after expiry."""
        dc = _get_dc(tmp_path)
        result = dc.simulate_stale_leader(ttl_seconds=1.0)

        assert result.leadership_recovered is True, (
            f"Stale leader recovery failed: leadership_recovered=False "
            f"details={result.incident_details}"
        )

    def test_lock_ttl_respected(self, tmp_path):
        """Stale lock scenario must confirm TTL was properly enforced."""
        dc = _get_dc(tmp_path)
        result = dc.simulate_stale_leader(ttl_seconds=1.0)

        assert result.lock_ttl_respected is True, (
            f"Lock TTL not respected: lock_ttl_respected=False "
            f"details={result.incident_details}"
        )

    def test_duplicate_leader_impossible(self, tmp_path):
        """Two LeaderElection instances for the same resource — at most 1 leader."""
        dc = _get_dc(tmp_path)
        result = dc.simulate_duplicate_leader(ttl_seconds=5.0)

        assert result.single_leader_maintained is True, (
            f"Duplicate leader detected: single_leader_maintained=False "
            f"details={result.incident_details}"
        )

    def test_no_split_brain_across_scenarios(self, tmp_path):
        """Run all scenarios and verify zero split-brain events."""
        dc = _get_dc(tmp_path)
        results = dc.run_all_scenarios()

        assert len(results) > 0, "run_all_scenarios() must return at least one result"
        no_split_brain = dc.assert_no_split_brain(results)

        assert no_split_brain is True, (
            "Split-brain detected across scenarios: "
            + str([r.scenario.value for r in results if r.split_brain_detected])
        )

    def test_lock_renewal_failure_correct(self, tmp_path):
        """Lock renewal must succeed before TTL expiry and fail after."""
        dc = _get_dc(tmp_path)
        result = dc.simulate_lock_renewal_failure(ttl_seconds=2.0)

        assert result.lock_ttl_respected is True, (
            f"Lock renewal behavior incorrect: lock_ttl_respected=False "
            f"details={result.incident_details}"
        )
