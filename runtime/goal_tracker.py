"""Goal Tracker — $98 → $50,000 progress with milestones and ETA.

Persists state to data/goal_tracker.json and appends snapshots to
data/goal_snapshots.jsonl so progress is never lost across restarts.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_GOAL_PATH      = "data/goal_tracker.json"
_DEFAULT_SNAPSHOTS_PATH = "data/goal_snapshots.jsonl"

MILESTONES = [200.0, 500.0, 1_000.0, 2_500.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0]


@dataclass
class GoalProgress:
    starting_balance:   float
    target:             float
    current_balance:    float
    total_gain_usd:     float
    total_gain_pct:     float
    multiplier_needed:  float   # target / starting_balance
    multiplier_achieved: float  # current / starting_balance
    progress_pct:       float   # 0–100 toward target
    next_milestone:     Optional[float]
    milestones_hit:     list
    days_running:       float
    avg_daily_pct:      float   # average daily return %
    eta_days:           Optional[int]
    last_updated:       str


class GoalTracker:
    """Tracks progress from starting_balance toward target.

    Thread-safe.  Persists to JSON on every update.
    """

    def __init__(
        self,
        starting_balance: float = 98.0,
        target: float = 50_000.0,
        goal_path: str = _DEFAULT_GOAL_PATH,
        snapshots_path: str = _DEFAULT_SNAPSHOTS_PATH,
    ) -> None:
        self._starting_balance = starting_balance
        self._target           = target
        self._goal_path        = goal_path
        self._snapshots_path   = snapshots_path
        self._lock             = threading.Lock()

        # Persisted state
        self._current_balance: float = starting_balance
        self._milestones_hit:  list  = []
        self._start_ts:        float = time.time()
        self._daily_returns:   list  = []   # list of daily return % values
        self._last_balance:    float = starting_balance
        self._last_ts:         float = time.time()

        self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def update(self, current_balance: float) -> GoalProgress:
        """Record new balance and persist. Returns current progress."""
        with self._lock:
            now = time.time()
            # Record daily return if at least 1 hour has passed
            elapsed_h = (now - self._last_ts) / 3600.0
            if elapsed_h >= 1.0 and self._last_balance > 0:
                pct_change = (current_balance - self._last_balance) / self._last_balance * 100.0
                # Normalise to daily: scale from elapsed hours
                daily_equiv = pct_change * (24.0 / elapsed_h)
                self._daily_returns.append(daily_equiv)
                if len(self._daily_returns) > 90:   # keep 90-day rolling window
                    self._daily_returns = self._daily_returns[-90:]
                self._last_balance = current_balance
                self._last_ts      = now

            self._current_balance = current_balance

            # Check milestones
            for ms in MILESTONES:
                if current_balance >= ms and ms not in self._milestones_hit:
                    self._milestones_hit.append(ms)

            progress = self._build_progress(current_balance)
            self._persist(progress)
            return progress

    def get_progress(self, current_balance: Optional[float] = None) -> GoalProgress:
        """Return progress without recording. Uses last known balance if not provided."""
        with self._lock:
            bal = current_balance if current_balance is not None else self._current_balance
            return self._build_progress(bal)

    def get_starting_balance(self) -> float:
        return self._starting_balance

    def get_target(self) -> float:
        return self._target

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_progress(self, current_balance: float) -> GoalProgress:
        gain_usd  = current_balance - self._starting_balance
        gain_pct  = (gain_usd / self._starting_balance * 100.0) if self._starting_balance > 0 else 0.0
        mult_need = self._target / self._starting_balance if self._starting_balance > 0 else 0.0
        mult_done = current_balance / self._starting_balance if self._starting_balance > 0 else 1.0
        prog_pct  = min(100.0, (current_balance - self._starting_balance) /
                        max(1.0, self._target - self._starting_balance) * 100.0)

        next_ms = next((m for m in MILESTONES if m > current_balance), None)
        days_running = (time.time() - self._start_ts) / 86400.0

        # ETA: extrapolate from average daily return
        avg_daily = (sum(self._daily_returns) / len(self._daily_returns)
                     if self._daily_returns else 0.0)
        eta_days: Optional[int] = None
        if avg_daily > 0 and current_balance < self._target:
            remaining_mult = self._target / max(1.0, current_balance)
            import math
            # balance * (1 + avg_daily/100)^n = target → n = log(target/balance) / log(1 + r)
            try:
                r = avg_daily / 100.0
                eta_days = max(1, int(math.ceil(math.log(remaining_mult) / math.log(1.0 + r))))
            except (ValueError, ZeroDivisionError):
                eta_days = None

        return GoalProgress(
            starting_balance=self._starting_balance,
            target=self._target,
            current_balance=round(current_balance, 2),
            total_gain_usd=round(gain_usd, 2),
            total_gain_pct=round(gain_pct, 2),
            multiplier_needed=round(mult_need, 1),
            multiplier_achieved=round(mult_done, 4),
            progress_pct=round(prog_pct, 4),
            next_milestone=next_ms,
            milestones_hit=sorted(self._milestones_hit),
            days_running=round(days_running, 2),
            avg_daily_pct=round(avg_daily, 4),
            eta_days=eta_days,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

    def _persist(self, progress: GoalProgress) -> None:
        data = {
            "starting_balance": self._starting_balance,
            "target":           self._target,
            "current_balance":  self._current_balance,
            "milestones_hit":   self._milestones_hit,
            "start_ts":         self._start_ts,
            "daily_returns":    self._daily_returns,
            "last_balance":     self._last_balance,
            "last_ts":          self._last_ts,
        }
        try:
            path = Path(self._goal_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

        # Append snapshot
        try:
            snap_path = Path(self._snapshots_path)
            snap_path.parent.mkdir(parents=True, exist_ok=True)
            with open(snap_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(progress)) + "\n")
        except Exception:
            pass

    def _load(self) -> None:
        try:
            path = Path(self._goal_path)
            if not path.exists():
                return
            data = json.loads(path.read_text())
            self._current_balance = float(data.get("current_balance", self._starting_balance))
            self._milestones_hit  = list(data.get("milestones_hit", []))
            self._start_ts        = float(data.get("start_ts", time.time()))
            self._daily_returns   = list(data.get("daily_returns", []))
            self._last_balance    = float(data.get("last_balance", self._current_balance))
            self._last_ts         = float(data.get("last_ts", time.time()))
        except Exception:
            pass


# ── Module singleton ──────────────────────────────────────────────────────────

_tracker: Optional[GoalTracker] = None
_tracker_lock = threading.Lock()


def get_goal_tracker(
    starting_balance: float = 98.0,
    target: float = 50_000.0,
) -> GoalTracker:
    """Return the process-level GoalTracker singleton."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = GoalTracker(
                    starting_balance=starting_balance,
                    target=target,
                )
    return _tracker
