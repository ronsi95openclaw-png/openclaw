#!/usr/bin/env python3
"""Generate DriftEngine backtest baseline from live outcomes or synthetic data.

Reads data/logs/trade_outcomes.jsonl. If >= 30 records exist, the oldest 60%
become the backtest baseline (chronological split). If < 30 records, synthetic
backtest data is generated from known historical strategy performance parameters.

Synthetic parameters (conservative, representative of strategy backtests):
    EMA_CROSS:       win_rate=0.52, mean_pnl=+8.5,  std_pnl=22.0
    BREAKOUT:        win_rate=0.48, mean_pnl=+3.2,  std_pnl=35.0
    TREND_FOLLOW:    win_rate=0.45, mean_pnl=-2.1,  std_pnl=40.0
    BOLLINGER_BAND:  win_rate=0.51, mean_pnl=+5.8,  std_pnl=18.0
    MEAN_REVERSION:  win_rate=0.55, mean_pnl=+6.2,  std_pnl=15.0

All synthetic data is labeled with "synthetic": true, "source": "backtest_simulator".
Random seed: 42 (deterministic). Minimum 30 records guaranteed.

Usage:
    python scripts/generate_backtest_baseline.py [--output PATH] [--min-records N] [--force]

    --output PATH     Output path (default: data/logs/backtest_outcomes.jsonl)
    --min-records N   Minimum records to write (default: 30)
    --force           Overwrite existing file (default: skip if exists)
    --dry-run         Print records to stdout without writing
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import random
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

# ---------------------------------------------------------------------------
# Strategy parameters — conservative, representative of historical backtests
# ---------------------------------------------------------------------------

STRATEGY_PARAMS: dict[str, dict] = {
    "EMA_CROSS": {
        "win_rate": 0.52,
        "mean_pnl": 8.5,
        "std_pnl": 22.0,
    },
    "BREAKOUT": {
        "win_rate": 0.48,
        "mean_pnl": 3.2,
        "std_pnl": 35.0,
    },
    "TREND_FOLLOW": {
        "win_rate": 0.45,
        "mean_pnl": -2.1,
        "std_pnl": 40.0,
    },
    "BOLLINGER_BAND": {
        "win_rate": 0.51,
        "mean_pnl": 5.8,
        "std_pnl": 18.0,
    },
    "MEAN_REVERSION": {
        "win_rate": 0.55,
        "mean_pnl": 6.2,
        "std_pnl": 15.0,
    },
}

REGIMES = ["TRENDING_BULL", "RANGING", "TRENDING_BEAR", "UNKNOWN"]
SIDES = ["long", "short"]

# Base timestamp: start synthetic records 90 days before today
_BASE_TS = datetime(2026, 2, 22, 0, 0, 0, tzinfo=timezone.utc)


def _normal(rng: random.Random, mu: float, sigma: float) -> float:
    """Box-Muller normal sample using the private rng."""
    # Use gauss — it is not synchronized with global random
    return rng.gauss(mu, sigma)


def _generate_synthetic_records(
    rng: random.Random,
    min_records: int = 30,
) -> List[dict]:
    """Generate synthetic backtest records deterministically.

    Records are distributed proportionally across all strategies.
    Each strategy gets at least ceil(min_records / num_strategies) records.

    Args:
        rng: Seeded Random instance (never uses global random).
        min_records: Minimum total record count.

    Returns:
        List of record dicts with all required DriftEngine fields.
    """
    strategies = list(STRATEGY_PARAMS.keys())
    n_strategies = len(strategies)

    # Ensure each strategy gets at least floor(min_records/n_strategies) + adjustment
    per_strategy = math.ceil(min_records / n_strategies)
    # Always produce at least 6 per strategy so distribution is meaningful
    per_strategy = max(per_strategy, 6)

    records: List[dict] = []
    regime_cycle = 0
    ts = _BASE_TS

    for strategy in strategies:
        params = STRATEGY_PARAMS[strategy]
        win_rate = params["win_rate"]
        mean_pnl = params["mean_pnl"]
        std_pnl = params["std_pnl"]

        for i in range(per_strategy):
            is_win = rng.random() < win_rate
            outcome = "win" if is_win else "loss"

            if is_win:
                # Win: positive pnl drawn from distribution, ensure >= 0
                raw = _normal(rng, abs(mean_pnl) if mean_pnl > 0 else abs(mean_pnl) * 0.5, std_pnl)
                pnl = abs(raw) if raw != 0 else 0.01
            else:
                # Loss: negative pnl — mirror of the distribution
                abs_loss = abs(mean_pnl) * 1.5 if mean_pnl < 0 else abs(mean_pnl)
                raw = _normal(rng, abs_loss, std_pnl)
                pnl = -abs(raw) if raw != 0 else -0.01

            pnl = round(pnl, 4)

            confidence = round(rng.uniform(0.5, 1.0), 4)
            regime = REGIMES[regime_cycle % len(REGIMES)]
            regime_cycle += 1

            side = rng.choice(SIDES)

            # Spread records across ~90 days; one record every ~12h on average
            ts += timedelta(hours=rng.uniform(6, 18))

            record: dict = {
                "ts": ts.isoformat(),
                "id": f"SYN-{uuid.UUID(int=rng.getrandbits(128)).hex[:12].upper()}",
                "strategy": strategy,
                "side": side,
                "outcome": outcome,
                "pnl": pnl,
                "confidence": confidence,
                "regime": regime,
                "synthetic": True,
                "source": "backtest_simulator",
                "demo": True,
            }
            records.append(record)

    # Shuffle so strategies are interleaved (preserves determinism via seeded rng)
    rng.shuffle(records)

    # Re-sort by ts after shuffle to maintain chronological order
    records.sort(key=lambda r: r["ts"])

    return records


def _load_live_records(live_path: str) -> List[dict]:
    """Load records from trade_outcomes.jsonl. Returns empty list on any error."""
    records: List[dict] = []
    if not os.path.exists(live_path):
        return records
    try:
        with open(live_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # skip malformed lines
    except OSError:
        pass
    return records


def _write_atomic(output_path: str, records: List[dict]) -> None:
    """Atomically write records to output_path using fcntl.LOCK_EX + os.replace.

    Algorithm:
    1. Write all records to a named tempfile in the same directory.
    2. Acquire LOCK_EX on the tempfile.
    3. os.replace(tmp, output_path) — atomic on POSIX.

    Args:
        output_path: Destination file path.
        records: List of record dicts; each written as a JSONL line.
    """
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=output_dir, prefix=".backtest_tmp_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            for record in records:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, output_path)
    except Exception:
        # Clean up tempfile on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _print_summary(records: List[dict], output_path: Optional[str] = None) -> None:
    """Print a human-readable summary of the generated records."""
    if not records:
        print("  No records generated.")
        return

    pnl_values = [r["pnl"] for r in records if isinstance(r.get("pnl"), (int, float))]
    mean_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0

    strategy_counts: dict[str, int] = {}
    for r in records:
        s = r.get("strategy", "UNKNOWN")
        strategy_counts[s] = strategy_counts.get(s, 0) + 1

    win_count = sum(1 for r in records if r.get("outcome") == "win")
    loss_count = sum(1 for r in records if r.get("outcome") == "loss")

    print(f"  Records written : {len(records)}")
    if output_path:
        print(f"  Output path     : {output_path}")
    print(f"  Mean PnL        : {mean_pnl:+.4f}")
    print(f"  Wins / Losses   : {win_count} / {loss_count}")
    print("  Strategy distribution:")
    for strategy, count in sorted(strategy_counts.items()):
        print(f"    {strategy:<20s} {count:3d} records")


# ---------------------------------------------------------------------------
# Public API — callable from tests and other modules
# ---------------------------------------------------------------------------

def generate_baseline(
    output_path: str = "data/logs/backtest_outcomes.jsonl",
    min_records: int = 30,
    seed: int = 42,
    force: bool = False,
    dry_run: bool = False,
) -> List[dict]:
    """Generate DriftEngine backtest baseline records.

    Reads data/logs/trade_outcomes.jsonl (relative to CWD). If the live file
    has >= 30 records, the oldest 60% become the baseline (chronological split).
    Otherwise, synthetic data is generated from historical strategy parameters.

    All synthetic records carry "synthetic": true, "source": "backtest_simulator".
    The random seed is fixed (default 42) for deterministic output.

    Args:
        output_path: Destination JSONL path.
        min_records: Minimum records to write (default: 30).
        seed: Random seed for synthetic generation (default: 42).
        force: If False and output_path exists, skip write and return existing records.
        dry_run: If True, do not write to disk; return generated records.

    Returns:
        List of record dicts that were (or would be) written.
    """
    # Check for existing file
    if not dry_run and not force and os.path.exists(output_path):
        print(f"Already exists, use --force to overwrite: {output_path}")
        # Return existing records so callers can inspect them
        existing: List[dict] = []
        try:
            with open(output_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            existing.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass
        return existing

    rng = random.Random(seed)

    # Try to load live records for chronological split
    live_path = "data/logs/trade_outcomes.jsonl"
    live_records = _load_live_records(live_path)

    records: List[dict]

    if len(live_records) >= 30:
        # Use oldest 60% as backtest baseline
        live_records_sorted = sorted(live_records, key=lambda r: r.get("ts", ""))
        split_idx = int(len(live_records_sorted) * 0.60)
        baseline_from_live = live_records_sorted[:split_idx]

        # Ensure all records have required DriftEngine fields
        records = []
        for r in baseline_from_live:
            rec = dict(r)
            rec.setdefault("synthetic", False)
            rec.setdefault("source", "live_trade")
            records.append(rec)

        # Top up with synthetic records if still below min_records
        if len(records) < min_records:
            synthetic = _generate_synthetic_records(rng, min_records - len(records))
            records.extend(synthetic)
    else:
        # Fewer than 30 live records — generate synthetic baseline
        records = _generate_synthetic_records(rng, min_records)

    # Guarantee minimum
    if len(records) < min_records:
        extra = _generate_synthetic_records(rng, min_records - len(records))
        records.extend(extra)

    if dry_run:
        _print_summary(records)
        return records

    _write_atomic(output_path, records)
    print(f"Backtest baseline written:")
    _print_summary(records, output_path)
    return records


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate DriftEngine backtest baseline (data/logs/backtest_outcomes.jsonl)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output",
        default="data/logs/backtest_outcomes.jsonl",
        metavar="PATH",
        help="Output JSONL path (default: data/logs/backtest_outcomes.jsonl)",
    )
    parser.add_argument(
        "--min-records",
        type=int,
        default=30,
        metavar="N",
        help="Minimum records to write (default: 30)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        metavar="SEED",
        help="Random seed for deterministic generation (default: 42)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing file (default: skip if exists)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print records to stdout without writing",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    generate_baseline(
        output_path=args.output,
        min_records=args.min_records,
        seed=args.seed,
        force=args.force,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
