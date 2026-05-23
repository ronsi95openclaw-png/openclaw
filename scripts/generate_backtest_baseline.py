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

    --output PATH         Output path (default: data/logs/backtest_outcomes.jsonl)
    --min-records N       Minimum records to write (default: 30)
    --force               Overwrite existing file (default: skip if exists)
    --dry-run             Print records to stdout without writing
    --import-jsonl PATH   Import real trade outcomes from an external JSONL file
    --import-csv PATH     Import real trade outcomes from a CSV file
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import random
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

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

# Paths for import audit output
_IMPORT_ERRORS_PATH = "data/logs/backtest_import_errors.jsonl"
_IMPORT_CHECKSUM_PATH = "data/logs/backtest_outcomes_checksum.json"

# Required fields and their expected types for imported records
_REQUIRED_FIELDS: dict[str, type] = {
    "pnl": (int, float),
    "strategy": str,
    "outcome": str,
    "ts": str,
}
_VALID_OUTCOMES = {"win", "loss"}


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
# Import validation and ingestion
# ---------------------------------------------------------------------------

def validate_record(record: dict, row_index: int) -> Tuple[bool, str]:
    """Validate that a record has all required fields with correct types.

    Required fields: pnl (float), strategy (str), outcome (str: "win"|"loss"),
    ts (ISO8601 str).

    Args:
        record: Dict representation of the trade record.
        row_index: 0-based row index for error messages.

    Returns:
        (is_valid, error_message) — error_message is "" when is_valid is True.
    """
    for field_name, expected_types in _REQUIRED_FIELDS.items():
        if field_name not in record:
            return False, f"row {row_index}: missing required field '{field_name}'"
        value = record[field_name]
        if not isinstance(value, expected_types):
            return False, (
                f"row {row_index}: field '{field_name}' has type "
                f"{type(value).__name__}, expected {expected_types}"
            )

    # Validate outcome value
    outcome = record["outcome"]
    if outcome not in _VALID_OUTCOMES:
        return False, (
            f"row {row_index}: 'outcome' must be one of {_VALID_OUTCOMES}, "
            f"got {outcome!r}"
        )

    # Validate ts is a parseable ISO8601 string
    ts_str = record["ts"]
    try:
        datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        return False, f"row {row_index}: 'ts' is not valid ISO8601: {ts_str!r} ({exc})"

    # Validate pnl is finite
    pnl = record["pnl"]
    if math.isnan(float(pnl)) or math.isinf(float(pnl)):
        return False, f"row {row_index}: 'pnl' must be finite, got {pnl!r}"

    return True, ""


def _dedup_records(records: List[dict]) -> List[dict]:
    """Deduplicate records by 'id' field if present, else by (ts, strategy, pnl) tuple.

    First occurrence wins. Preserves relative order before sorting.
    """
    seen: set = set()
    result: List[dict] = []
    for r in records:
        if "id" in r and r["id"]:
            key = ("id", r["id"])
        else:
            key = ("composite", r["ts"], r["strategy"], float(r["pnl"]))
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


def import_from_jsonl(path: str, rng: random.Random) -> Tuple[List[dict], List[dict]]:
    """Import and validate records from an external JSONL file.

    Reads line by line. Each line must be a valid JSON object satisfying the
    required schema. Invalid lines are collected as error records but never
    silently dropped.

    Args:
        path: Path to the external JSONL file.
        rng: Seeded Random instance (passed for future use / synthetic padding).

    Returns:
        (valid_records, error_records) — error_records contain the raw line
        and a descriptive error_message field.
    """
    valid: List[dict] = []
    errors: List[dict] = []

    if not os.path.exists(path):
        errors.append({
            "source_path": path,
            "row_index": -1,
            "raw_line": "",
            "error_message": f"File not found: {path}",
            "import_ts": datetime.now(timezone.utc).isoformat(),
        })
        return valid, errors

    with open(path, "r", encoding="utf-8") as fh:
        for row_index, line in enumerate(fh):
            raw = line.rstrip("\n")
            if not raw.strip():
                continue  # skip blank lines silently

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append({
                    "source_path": path,
                    "row_index": row_index,
                    "raw_line": raw[:500],
                    "error_message": f"row {row_index}: JSON decode error: {exc}",
                    "import_ts": datetime.now(timezone.utc).isoformat(),
                })
                continue

            if not isinstance(record, dict):
                errors.append({
                    "source_path": path,
                    "row_index": row_index,
                    "raw_line": raw[:500],
                    "error_message": f"row {row_index}: record is not a JSON object",
                    "import_ts": datetime.now(timezone.utc).isoformat(),
                })
                continue

            is_valid, error_msg = validate_record(record, row_index)
            if not is_valid:
                errors.append({
                    "source_path": path,
                    "row_index": row_index,
                    "raw_line": raw[:500],
                    "error_message": error_msg,
                    "import_ts": datetime.now(timezone.utc).isoformat(),
                })
                continue

            # Mark as real import
            record = dict(record)
            record["synthetic"] = False
            record["source"] = "real_import"
            valid.append(record)

    valid = _dedup_records(valid)
    valid.sort(key=lambda r: r.get("ts", ""))
    return valid, errors


def import_from_csv(path: str, rng: random.Random) -> Tuple[List[dict], List[dict]]:
    """Import and validate records from an external CSV file.

    The CSV must have a header row. Column names are matched case-insensitively
    to the required fields: pnl, strategy, outcome, ts. Extra columns are kept
    as additional metadata. Numeric fields are coerced from string.

    Args:
        path: Path to the external CSV file.
        rng: Seeded Random instance (passed for future use / synthetic padding).

    Returns:
        (valid_records, error_records)
    """
    valid: List[dict] = []
    errors: List[dict] = []

    if not os.path.exists(path):
        errors.append({
            "source_path": path,
            "row_index": -1,
            "raw_line": "",
            "error_message": f"File not found: {path}",
            "import_ts": datetime.now(timezone.utc).isoformat(),
        })
        return valid, errors

    with open(path, "r", encoding="utf-8", newline="") as fh:
        try:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                errors.append({
                    "source_path": path,
                    "row_index": -1,
                    "raw_line": "",
                    "error_message": "CSV file has no header row or is empty",
                    "import_ts": datetime.now(timezone.utc).isoformat(),
                })
                return valid, errors

            # Build a case-insensitive column map: lower(col) -> original col name
            col_map = {col.lower().strip(): col for col in (reader.fieldnames or [])}

            # Verify required columns present (case-insensitive)
            missing_cols = [f for f in _REQUIRED_FIELDS if f.lower() not in col_map]
            if missing_cols:
                errors.append({
                    "source_path": path,
                    "row_index": -1,
                    "raw_line": "",
                    "error_message": (
                        f"CSV missing required columns: {missing_cols}. "
                        f"Found columns: {list(reader.fieldnames)}"
                    ),
                    "import_ts": datetime.now(timezone.utc).isoformat(),
                })
                return valid, errors

            for row_index, raw_row in enumerate(reader):
                # Build a normalized record with lower-cased keys
                record: dict = {}
                for orig_col, value in raw_row.items():
                    if orig_col is None:
                        continue
                    record[orig_col.lower().strip()] = value.strip() if isinstance(value, str) else value

                # Coerce pnl to float
                try:
                    record["pnl"] = float(record["pnl"])
                except (ValueError, KeyError) as exc:
                    errors.append({
                        "source_path": path,
                        "row_index": row_index,
                        "raw_line": json.dumps(dict(raw_row))[:500],
                        "error_message": f"row {row_index}: cannot coerce 'pnl' to float: {exc}",
                        "import_ts": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                is_valid, error_msg = validate_record(record, row_index)
                if not is_valid:
                    errors.append({
                        "source_path": path,
                        "row_index": row_index,
                        "raw_line": json.dumps(dict(raw_row))[:500],
                        "error_message": error_msg,
                        "import_ts": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                # Mark as real import
                record["synthetic"] = False
                record["source"] = "real_import"
                valid.append(record)

        except csv.Error as exc:
            errors.append({
                "source_path": path,
                "row_index": -1,
                "raw_line": "",
                "error_message": f"CSV parse error: {exc}",
                "import_ts": datetime.now(timezone.utc).isoformat(),
            })
            return valid, errors

    valid = _dedup_records(valid)
    valid.sort(key=lambda r: r.get("ts", ""))
    return valid, errors


def compute_import_checksum(records: List[dict]) -> str:
    """Compute SHA256 of JSON-serialized pnl values sorted by ts.

    Records are sorted by ts (ascending) before extracting pnl values.
    The checksum is stable: same records in any order produce the same hash.

    Args:
        records: List of validated record dicts each containing 'ts' and 'pnl'.

    Returns:
        Lowercase hex SHA256 string.
    """
    sorted_records = sorted(records, key=lambda r: r.get("ts", ""))
    pnl_list = [float(r["pnl"]) for r in sorted_records]
    payload = json.dumps(pnl_list, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_checksum(output_path: str, checksum: str, record_count: int) -> None:
    """Write checksum metadata to data/logs/backtest_outcomes_checksum.json.

    Uses atomic tmp+replace write with fcntl.LOCK_EX.

    Args:
        output_path: Source JSONL path the checksum was computed from.
        checksum: SHA256 hex string.
        record_count: Number of records the checksum covers.
    """
    checksum_dir = os.path.dirname(os.path.abspath(_IMPORT_CHECKSUM_PATH))
    os.makedirs(checksum_dir, exist_ok=True)

    metadata = {
        "checksum": checksum,
        "record_count": record_count,
        "source_path": output_path,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "sha256",
        "description": "SHA256 of JSON-serialized pnl values sorted by ts ascending",
    }

    fd, tmp_path = tempfile.mkstemp(
        dir=checksum_dir,
        prefix=".checksum_tmp_",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(json.dumps(metadata, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, _IMPORT_CHECKSUM_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def log_import_errors(errors: List[dict]) -> None:
    """Append error records to data/logs/backtest_import_errors.jsonl.

    Uses atomic tempfile write then appends to the target via fcntl.LOCK_EX.
    Each error dict is written as a single JSONL line. Never raises — errors
    in the error logger itself are printed to stderr.

    Args:
        errors: List of error dicts (one per invalid row).
    """
    if not errors:
        return

    errors_dir = os.path.dirname(os.path.abspath(_IMPORT_ERRORS_PATH))
    os.makedirs(errors_dir, exist_ok=True)

    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=errors_dir,
            prefix=".import_errors_tmp_",
            suffix=".jsonl",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                for err in errors:
                    fh.write(json.dumps(err, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

            # Append tmp content to the audit log
            with open(_IMPORT_ERRORS_PATH, "ab") as dest_fh:
                fcntl.flock(dest_fh.fileno(), fcntl.LOCK_EX)
                with open(tmp_path, "rb") as src_fh:
                    dest_fh.write(src_fh.read())
                dest_fh.flush()
                os.fsync(dest_fh.fileno())
                fcntl.flock(dest_fh.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        print(f"[ERROR] Failed to log import errors to {_IMPORT_ERRORS_PATH}: {exc}", file=sys.stderr)


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


def import_and_write(
    import_path: str,
    import_format: str,
    output_path: str = "data/logs/backtest_outcomes.jsonl",
    min_records: int = 30,
    seed: int = 42,
    force: bool = False,
    dry_run: bool = False,
) -> List[dict]:
    """Import records from an external file and write the backtest baseline.

    This is the main entry point when --import-jsonl or --import-csv is supplied.

    Workflow:
    1. Import and validate records from the external file.
    2. Log any errors to backtest_import_errors.jsonl (atomic, fcntl locked).
    3. Deduplicate (done inside import_from_* functions).
    4. Sort by ts ascending.
    5. Fail closed if 0 valid records produced.
    6. Pad with synthetic records if below min_records.
    7. Compute and write checksum.
    8. Write to output_path (atomic write).

    Args:
        import_path: Path to the external JSONL or CSV file.
        import_format: "jsonl" or "csv".
        output_path: Destination JSONL path.
        min_records: Minimum records to write (synthetic padding applied if needed).
        seed: Random seed for deterministic synthetic padding.
        force: If False and output_path exists, skip write.
        dry_run: If True, do not write to disk.

    Returns:
        List of record dicts that were (or would be) written.
    """
    if not dry_run and not force and os.path.exists(output_path):
        print(f"Already exists, use --force to overwrite: {output_path}")
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

    # Step 1: Import
    print(f"Importing {import_format.upper()} from: {import_path}")
    if import_format == "jsonl":
        valid_records, error_records = import_from_jsonl(import_path, rng)
    elif import_format == "csv":
        valid_records, error_records = import_from_csv(import_path, rng)
    else:
        print(f"[ERROR] Unknown import format: {import_format!r}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Log errors — NEVER silently drop invalid rows
    if error_records:
        print(
            f"  [WARN] {len(error_records)} invalid row(s) found — "
            f"logged to {_IMPORT_ERRORS_PATH}",
            file=sys.stderr,
        )
        log_import_errors(error_records)

    # Step 3: Fail closed if zero valid records
    if not valid_records:
        print(
            f"[ERROR] Import produced 0 valid records from {import_path}. "
            f"See {_IMPORT_ERRORS_PATH} for details.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Imported {len(valid_records)} valid record(s).")

    # Step 4: Sort by ts ascending (deterministic ordering)
    valid_records.sort(key=lambda r: r.get("ts", ""))

    # Step 5: Pad with synthetic records if below min_records
    records = list(valid_records)
    if len(records) < min_records:
        shortage = min_records - len(records)
        print(
            f"  [INFO] Only {len(records)} valid record(s) — padding with "
            f"{shortage} synthetic record(s) to meet min_records={min_records}."
        )
        synthetic = _generate_synthetic_records(rng, shortage)
        # Label synthetic padding distinctly
        for r in synthetic:
            r["source"] = "synthetic_padding"
        records.extend(synthetic)
        # Re-sort after padding
        records.sort(key=lambda r: r.get("ts", ""))

    # Step 6: Compute and write checksum (over real import records only)
    checksum = compute_import_checksum(valid_records)
    print(f"  Import checksum (sha256): {checksum}")
    if not dry_run:
        write_checksum(output_path, checksum, len(valid_records))

    if dry_run:
        _print_summary(records)
        return records

    # Step 7: Write output
    _write_atomic(output_path, records)
    print(f"Backtest baseline written from import:")
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
    parser.add_argument(
        "--import-jsonl",
        default=None,
        metavar="PATH",
        dest="import_jsonl",
        help="Import real trade outcomes from an external JSONL file",
    )
    parser.add_argument(
        "--import-csv",
        default=None,
        metavar="PATH",
        dest="import_csv",
        help="Import real trade outcomes from a CSV file",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Mutual exclusion: cannot supply both --import-jsonl and --import-csv
    if args.import_jsonl and args.import_csv:
        parser.error("Cannot use --import-jsonl and --import-csv at the same time.")

    if args.import_jsonl:
        import_and_write(
            import_path=args.import_jsonl,
            import_format="jsonl",
            output_path=args.output,
            min_records=args.min_records,
            seed=args.seed,
            force=args.force,
            dry_run=args.dry_run,
        )
    elif args.import_csv:
        import_and_write(
            import_path=args.import_csv,
            import_format="csv",
            output_path=args.output,
            min_records=args.min_records,
            seed=args.seed,
            force=args.force,
            dry_run=args.dry_run,
        )
    else:
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
