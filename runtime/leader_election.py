"""Leader Election for OpenClaw — single-leader coordination via DistributedLock.

Provides periodic election with callbacks for leadership transitions.
Degrades gracefully to single-node mode if DistributedLock is unavailable.

Key properties:
- Daemon election thread: periodic renew/acquire cycle.
- Non-blocking callbacks: fired in a separate thread, wrapped in try/except.
- Graceful single-node fallback: assumes LEADER if lock system unavailable.
- Fail-CLOSED: any unexpected lock state → FOLLOWER (not assumed LEADER).

Module singleton: get_election(node_id) -> LeaderElection
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, List, Optional

logger = logging.getLogger("openclaw.runtime.leader_election")

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_RESOURCE_NAME   = "openclaw-leader"
_DEFAULT_TTL_SECONDS     = 30
_DEFAULT_ELECTION_INTERVAL_S = 15


# ── Enums ─────────────────────────────────────────────────────────────────────

class LeaderState(str, Enum):
    LEADER    = "LEADER"
    FOLLOWER  = "FOLLOWER"
    CANDIDATE = "CANDIDATE"
    UNKNOWN   = "UNKNOWN"


# ── LeaderElection ────────────────────────────────────────────────────────────

class LeaderElection:
    """Periodic leader election using a DistributedLock.

    On startup, begins a daemon thread that attempts to acquire or renew the
    leader lock every election_interval_s seconds.

    Callbacks (on_become_leader, on_lose_leadership) are fired in a non-blocking
    daemon thread to avoid stalling the election loop.

    Single-node fallback: if DistributedLock initialization fails, the node
    assumes it is LEADER and is_leader() always returns True.
    """

    def __init__(
        self,
        node_id: str,
        resource_name: str = _DEFAULT_RESOURCE_NAME,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        election_interval_s: float = _DEFAULT_ELECTION_INTERVAL_S,
    ) -> None:
        if not node_id or not node_id.strip():
            raise ValueError("node_id must be a non-empty string")

        self._node_id              = node_id
        self._resource_name        = resource_name
        self._ttl_seconds          = ttl_seconds
        self._election_interval_s  = election_interval_s

        self._state       = LeaderState.UNKNOWN
        self._state_lock  = threading.Lock()
        self._running     = False
        self._thread: Optional[threading.Thread] = None
        self._single_node_mode = False

        self._become_leader_callbacks: List[Callable] = []
        self._lose_leadership_callbacks: List[Callable] = []

        # Epoch tracking (incremented each time this node becomes leader)
        self._epoch: int = 0
        self._epoch_lock = threading.Lock()

        # Initialize distributed lock
        self._lock = self._init_lock()

    def _init_lock(self):
        """Initialize DistributedLock, falling back to single-node mode on failure."""
        try:
            from runtime.distributed_lock import DistributedLock
            lock = DistributedLock(
                resource_name=self._resource_name,
                ttl_seconds=self._ttl_seconds,
            )
            logger.info(
                "LeaderElection initialized for node=%s resource=%s",
                self._node_id, self._resource_name,
            )
            return lock
        except Exception as exc:
            logger.warning(
                "DistributedLock init failed — assuming single-node LEADER mode: %s", exc
            )
            self._single_node_mode = True
            with self._state_lock:
                self._state = LeaderState.LEADER
            return None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin periodic election attempts in a daemon thread."""
        if self._running:
            logger.warning("LeaderElection.start() called while already running")
            return

        self._running = True

        if self._single_node_mode:
            logger.info(
                "Single-node mode: node=%s is permanently LEADER", self._node_id
            )
            return

        with self._state_lock:
            self._state = LeaderState.CANDIDATE

        self._thread = threading.Thread(
            target=self._election_loop,
            name=f"leader-election-{self._node_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Election loop started for node=%s", self._node_id)

    def stop(self) -> None:
        """Gracefully stop the election loop and release leadership if held."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._election_interval_s + 2)

        # Release lock if we hold it
        if self._lock is not None:
            try:
                if self._lock.is_held_by(self._node_id):
                    self._lock.release(self._node_id)
                    logger.info("Leadership released on stop: node=%s", self._node_id)
            except Exception as exc:
                logger.error("Error releasing lock on stop: %s", exc)

        with self._state_lock:
            self._state = LeaderState.FOLLOWER
        logger.info("Election loop stopped for node=%s", self._node_id)

    # ── Election loop ─────────────────────────────────────────────────────────

    def _election_loop(self) -> None:
        """Daemon thread: attempt to acquire or renew leader lock periodically."""
        while self._running:
            try:
                self._run_election_cycle()
            except Exception as exc:
                logger.error("Unexpected error in election loop: %s", exc)
                # Fail-CLOSED: drop to FOLLOWER on unexpected error
                self._transition_to(LeaderState.FOLLOWER)

            if not self._running:
                break
            time.sleep(self._election_interval_s)

    def _run_election_cycle(self) -> None:
        """One election attempt: try to acquire or renew."""
        if self._lock is None:
            return

        with self._state_lock:
            prev_state = self._state

        try:
            currently_held = self._lock.is_held_by(self._node_id)

            if currently_held:
                # Already leader — renew TTL
                renewed = self._lock.renew(self._node_id, self._ttl_seconds)
                if renewed:
                    if prev_state != LeaderState.LEADER:
                        logger.info("Node %s transitioned to LEADER", self._node_id)
                        self._transition_to(LeaderState.LEADER)
                        self._on_become_leader_with_epoch()
                    else:
                        logger.debug("Node %s renewed leadership", self._node_id)
                else:
                    # Renewal failed — lost leadership
                    logger.warning("Node %s lost leadership (renew failed)", self._node_id)
                    self._transition_to(LeaderState.FOLLOWER)
                    if prev_state == LeaderState.LEADER:
                        self._fire_lose_leadership()
            else:
                # Try to acquire
                acquired = self._lock.acquire(self._node_id)
                if acquired:
                    logger.info("Node %s acquired leadership", self._node_id)
                    if prev_state != LeaderState.LEADER:
                        self._transition_to(LeaderState.LEADER)
                        self._on_become_leader_with_epoch()
                else:
                    # Another node holds the lock
                    if prev_state == LeaderState.LEADER:
                        logger.warning("Node %s lost leadership (acquire failed)", self._node_id)
                        self._fire_lose_leadership()
                    self._transition_to(LeaderState.FOLLOWER)
                    logger.debug(
                        "Node %s is FOLLOWER, leader=%s",
                        self._node_id, self._lock.get_current_holder(),
                    )

        except Exception as exc:
            logger.error("Election cycle error for node=%s: %s", self._node_id, exc)
            # Fail-CLOSED: don't assume leadership on error
            if prev_state == LeaderState.LEADER:
                self._transition_to(LeaderState.FOLLOWER)
                self._fire_lose_leadership()
            else:
                self._transition_to(LeaderState.FOLLOWER)

    # ── Epoch management ──────────────────────────────────────────────────────

    def get_epoch(self) -> int:
        """Return the current leadership epoch (incremented each time we become leader)."""
        with self._epoch_lock:
            return self._epoch

    def _on_become_leader_with_epoch(self) -> None:
        """Increment epoch and fire the on_become_leader callbacks.

        Called from the election loop whenever this node transitions to LEADER.
        """
        with self._epoch_lock:
            self._epoch += 1
        self._fire_become_leader()

    # ── Health scoring ────────────────────────────────────────────────────────

    def get_quorum_health_score(self) -> float:
        """Return 0.0–1.0 quorum health score.

        1.0 = stable leadership (LEADER with epoch > 0)
        0.7 = first election / unknown prior state (LEADER, epoch == 0)
        0.5 = FOLLOWER
        0.3 = CANDIDATE
        0.0 = UNKNOWN
        1.0 = single_node_mode (stable by assumption)
        """
        if self._single_node_mode:
            return 1.0
        state = self.get_state()
        if state == LeaderState.LEADER:
            epoch = self.get_epoch()
            return 1.0 if epoch > 0 else 0.7
        if state == LeaderState.FOLLOWER:
            return 0.5
        if state == LeaderState.CANDIDATE:
            return 0.3
        # UNKNOWN or anything else
        return 0.0

    def get_status_extended(self) -> dict:
        """Return get_status() extended with epoch and quorum_health_score."""
        base = self.get_status()
        base["epoch"] = self.get_epoch()
        base["quorum_health_score"] = self.get_quorum_health_score()
        return base

    # ── State management ──────────────────────────────────────────────────────

    def _transition_to(self, new_state: LeaderState) -> None:
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            logger.info(
                "LeaderElection state: %s → %s (node=%s)",
                old.value, new_state.value, self._node_id,
            )

    # ── Public interface ──────────────────────────────────────────────────────

    def is_leader(self) -> bool:
        """True if this node currently holds leadership."""
        if self._single_node_mode:
            return True
        with self._state_lock:
            return self._state == LeaderState.LEADER

    def get_state(self) -> LeaderState:
        """Return current LeaderState."""
        if self._single_node_mode:
            return LeaderState.LEADER
        with self._state_lock:
            return self._state

    def get_leader(self) -> Optional[str]:
        """Return the current leader's node_id, or None if unknown."""
        if self._single_node_mode:
            return self._node_id
        if self._lock is None:
            return None
        try:
            return self._lock.get_current_holder()
        except Exception:
            return None

    def on_become_leader(self, callback: Callable) -> None:
        """Register a callback to fire when this node becomes leader."""
        self._become_leader_callbacks.append(callback)

    def on_lose_leadership(self, callback: Callable) -> None:
        """Register a callback to fire when this node loses leadership."""
        self._lose_leadership_callbacks.append(callback)

    def get_status(self) -> dict:
        """Return a status summary dict."""
        return {
            "node_id":          self._node_id,
            "state":            self.get_state().value,
            "is_leader":        self.is_leader(),
            "current_leader":   self.get_leader(),
            "resource_name":    self._resource_name,
            "ttl_seconds":      self._ttl_seconds,
            "election_interval_s": self._election_interval_s,
            "single_node_mode": self._single_node_mode,
            "running":          self._running,
        }

    # ── Callback dispatch ─────────────────────────────────────────────────────

    def _fire_become_leader(self) -> None:
        """Fire all on_become_leader callbacks in a non-blocking daemon thread."""
        self._dispatch_callbacks(self._become_leader_callbacks, "on_become_leader")

    def _fire_lose_leadership(self) -> None:
        """Fire all on_lose_leadership callbacks in a non-blocking daemon thread."""
        self._dispatch_callbacks(self._lose_leadership_callbacks, "on_lose_leadership")

    def _dispatch_callbacks(self, callbacks: List[Callable], label: str) -> None:
        if not callbacks:
            return

        def _run():
            for cb in callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.error("Callback error in %s: %s", label, exc)

        threading.Thread(target=_run, name=f"election-cb-{label}", daemon=True).start()


# ── Module singleton ──────────────────────────────────────────────────────────

_election: Optional[LeaderElection] = None
_election_lock = threading.Lock()


def get_election(node_id: Optional[str] = None) -> LeaderElection:
    """Return the module-level LeaderElection singleton.

    If node_id is provided and no singleton exists yet, creates one.
    If singleton already exists, returns it (node_id is ignored).
    """
    global _election
    if _election is None:
        with _election_lock:
            if _election is None:
                if not node_id:
                    import socket
                    node_id = f"{socket.gethostname()}-{id(object())}"
                    logger.warning(
                        "get_election() called with no node_id — using generated: %s",
                        node_id,
                    )
                _election = LeaderElection(node_id=node_id)
    return _election
