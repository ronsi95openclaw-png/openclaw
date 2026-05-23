"""Trade execution analytics subsystem for OpenClaw.

Tracks every fill event — slippage, latency, fill efficiency, rejections,
partial fills, maker/taker ratio — and produces structured reports that feed
into the nightly Claude Analyst review and the Prometheus metrics endpoint.

Thread-safe bounded deque (maxlen=500).  Persists one JSON record per trade to
data/execution_analytics.jsonl (fcntl-locked) and writes atomic JSON reports.

Usage
-----
    from runtime.execution_analytics import ExecutionAnalyticsEngine, ExecutionRecord

    engine = ExecutionAnalyticsEngine()
    engine.load_from_file("data/logs/trade_outcomes.jsonl")

    rec = ExecutionRecord(trade_id="T001", symbol="BTCUSD-PERP", ...)
    engine.record(rec)

    report = engine.generate_report()
    engine.persist_report("data/execution_analytics_report.json")
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.execution_analytics")

_ANALYTICS_JSONL = Path("data/execution_analytics.jsonl")

# ── Prometheus (optional) ─────────────────────────────────────────────────────

try:
    from runtime.metrics import get_registry as _get_registry  # type: ignore[import]
    _metrics = _get_registry()
except Exception:  # noqa: BLE001
    _metrics = None


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ExecutionRecord:
    """Immutable record of a single trade execution attempt."""

    trade_id:           str
    symbol:             str
    strategy:           str
    side:               str                   # "long" | "short"
    order_type:         str                   # "MARKET" | "STOP_LOSS" | "TAKE_PROFIT"

    # Pricing
    expected_price:     float = 0.0
    actual_price:       float = 0.0

    # Quantity
    expected_qty:       float = 0.0
    actual_qty:         float = 0.0

    # Timestamps (epoch ms, 0 = absent)
    entry_ts_ms:        float = 0.0          # time intent was submitted
    ack_ts_ms:          float = 0.0          # time exchange acknowledged order
    fill_ts_ms:         float = 0.0          # time order was fully filled
    cancel_ts_ms:       float = 0.0          # time order was cancelled (0 = not cancelled)

    # Spread snapshots
    spread_at_entry:    float = 0.0          # bid-ask spread (USD) at submission
    spread_at_exit:     float = 0.0          # bid-ask spread at fill

    # Derived (computed by engine if not supplied)
    slippage_bps:       float = 0.0

    # Fill quality flags
    is_maker:           bool  = False
    rejected:           bool  = False
    rejection_reason:   str   = ""
    timed_out:          bool  = False
    partial_fill:       bool  = False
    partial_fill_pct:   float = 100.0        # 100.0 = complete fill


@dataclass
class ExecutionAnalyticsReport:
    """Aggregated execution quality metrics over the in-memory window."""

    generated_at:              str   = ""

    # Volume
    total_trades:              int   = 0

    # Slippage
    avg_slippage_bps:          float = 0.0
    worst_slippage_bps:        float = 0.0

    # Fill
    fill_efficiency:           float = 1.0   # actual_qty / expected_qty average

    # Rejection / timeout / partial
    rejection_pct:             float = 0.0
    timeout_rate:              float = 0.0
    partial_fill_rate:         float = 0.0

    # Latency
    avg_latency_ms:            float = 0.0
    p95_latency_ms:            float = 0.0

    # Composite
    execution_stability_score: float = 1.0   # 0–1
    maker_pct:                 float = 0.0

    # Order-type quality
    stop_execution_quality:    float = 1.0   # 0–1
    tp_execution_quality:      float = 1.0   # 0–1

    # Breakdowns
    by_strategy:               Dict[str, Any] = field(default_factory=dict)
    by_symbol:                 Dict[str, Any] = field(default_factory=dict)


# ── Engine ────────────────────────────────────────────────────────────────────

class ExecutionAnalyticsEngine:
    """Thread-safe execution analytics engine.

    Parameters
    ----------
    maxlen :
        Maximum number of records held in memory (oldest are evicted first).
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._lock:    threading.Lock = threading.Lock()
        self._records: deque[ExecutionRecord] = deque(maxlen=maxlen)
        _ANALYTICS_JSONL.parent.mkdir(parents=True, exist_ok=True)

    # ── Public write API ──────────────────────────────────────────────────────

    def record(self, rec: ExecutionRecord) -> None:
        """Append an ExecutionRecord (thread-safe).

        Slippage is (re)computed if the record carries a non-zero expected and
        actual price and slippage_bps is still 0.0.
        Persists the record to data/execution_analytics.jsonl.
        """
        rec = self._compute_derived(rec)
        with self._lock:
            self._records.append(rec)
        self._persist_record(rec)
        self._emit_metrics(rec)

    def record_from_outcome(self, outcome_dict: Dict[str, Any]) -> Optional[ExecutionRecord]:
        """Parse a trade_outcomes.jsonl entry and record it.

        The trade_outcomes format used by CryptoComBot contains:
          id, symbol, strategy, side, entry_price, exit_price, size, demo, ts …

        Missing execution-specific fields (ack_ts_ms, actual_qty, etc.) are
        inferred or defaulted so that the record is still useful for aggregate
        analysis.

        Returns the created ExecutionRecord, or None if the dict is malformed.
        """
        try:
            trade_id = outcome_dict.get("id", "")
            symbol   = outcome_dict.get("symbol", "")
            strategy = outcome_dict.get("strategy", "")
            side     = outcome_dict.get("side", "long")

            entry_price = float(outcome_dict.get("entry_price", 0.0))
            exit_price  = float(outcome_dict.get("exit_price",  0.0))
            size        = float(outcome_dict.get("size",         0.0))

            # Parse entry timestamp → ms
            ts_str  = outcome_dict.get("ts", "")
            entry_ms: float = 0.0
            if ts_str:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                    entry_ms = dt.timestamp() * 1000
                except Exception:  # noqa: BLE001
                    pass

            # Infer order type from context clues
            order_type = "MARKET"
            signal = outcome_dict.get("signal_reason", "")
            if "stop" in signal.lower() or "sl" in signal.lower():
                order_type = "STOP_LOSS"
            elif "tp" in signal.lower() or "take_profit" in signal.lower():
                order_type = "TAKE_PROFIT"

            rec = ExecutionRecord(
                trade_id        = trade_id,
                symbol          = symbol,
                strategy        = strategy,
                side            = side,
                order_type      = order_type,
                expected_price  = entry_price,
                actual_price    = entry_price,   # no slippage data in outcome record
                expected_qty    = size,
                actual_qty      = size,
                entry_ts_ms     = entry_ms,
                ack_ts_ms       = entry_ms,
                fill_ts_ms      = entry_ms,
                slippage_bps    = 0.0,           # unknown from outcome record
                is_maker        = False,
                rejected        = False,
                partial_fill    = False,
                partial_fill_pct= 100.0,
            )

            self.record(rec)
            return rec

        except Exception as exc:  # noqa: BLE001
            logger.warning("record_from_outcome failed: %s — dict=%s", exc, outcome_dict)
            return None

    # ── Query API ─────────────────────────────────────────────────────────────

    def get_recent(self, n: int = 20) -> List[ExecutionRecord]:
        """Return the most recent *n* records (newest last)."""
        with self._lock:
            recs = list(self._records)
        return recs[-n:]

    # ── Analytics ─────────────────────────────────────────────────────────────

    def generate_report(self) -> ExecutionAnalyticsReport:
        """Compute all metrics over the current in-memory window.

        Returns an ExecutionAnalyticsReport.  Safe to call from any thread.
        """
        with self._lock:
            recs = list(self._records)

        report = ExecutionAnalyticsReport(
            generated_at = datetime.now(timezone.utc).isoformat(),
            total_trades = len(recs),
        )

        if not recs:
            return report

        # ── Per-record aggregations ────────────────────────────────────────────

        slippages:       List[float] = []
        latencies_ms:    List[float] = []
        fill_effs:       List[float] = []
        rejected_count   = 0
        timeout_count    = 0
        partial_count    = 0
        maker_count      = 0

        sl_slippages:    List[float] = []   # for stop_execution_quality
        tp_slippages:    List[float] = []   # for tp_execution_quality

        by_strategy: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_slippage_bps": 0.0,
                     "rejections": 0, "fill_efficiency_sum": 0.0}
        )
        by_symbol: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_slippage_bps": 0.0,
                     "rejections": 0, "fill_efficiency_sum": 0.0}
        )

        for rec in recs:
            slippages.append(rec.slippage_bps)

            # Fill efficiency per record
            fe = (rec.actual_qty / rec.expected_qty
                  if rec.expected_qty > 0 else 1.0)
            fe = min(fe, 1.0)  # cap at 1.0
            fill_effs.append(fe)

            # Latency (entry_ts_ms → fill_ts_ms)
            if rec.entry_ts_ms > 0 and rec.fill_ts_ms > 0:
                latency = rec.fill_ts_ms - rec.entry_ts_ms
                if latency >= 0:
                    latencies_ms.append(latency)

            if rec.rejected:
                rejected_count += 1
            if rec.timed_out:
                timeout_count += 1
            if rec.partial_fill:
                partial_count += 1
            if rec.is_maker:
                maker_count += 1

            # Order-type quality
            if rec.order_type == "STOP_LOSS":
                sl_slippages.append(rec.slippage_bps)
            elif rec.order_type == "TAKE_PROFIT":
                tp_slippages.append(rec.slippage_bps)

            # Strategy breakdown
            s = rec.strategy or "UNKNOWN"
            by_strategy[s]["count"]               += 1
            by_strategy[s]["total_slippage_bps"]  += rec.slippage_bps
            by_strategy[s]["rejections"]           += int(rec.rejected)
            by_strategy[s]["fill_efficiency_sum"]  += fe

            # Symbol breakdown
            sym = rec.symbol or "UNKNOWN"
            by_symbol[sym]["count"]               += 1
            by_symbol[sym]["total_slippage_bps"]  += rec.slippage_bps
            by_symbol[sym]["rejections"]           += int(rec.rejected)
            by_symbol[sym]["fill_efficiency_sum"]  += fe

        n = len(recs)

        # ── Slippage ──────────────────────────────────────────────────────────
        report.avg_slippage_bps   = sum(slippages) / n if slippages else 0.0
        report.worst_slippage_bps = max(slippages) if slippages else 0.0

        # ── Fill efficiency ───────────────────────────────────────────────────
        report.fill_efficiency = sum(fill_effs) / n if fill_effs else 1.0

        # ── Rates ─────────────────────────────────────────────────────────────
        report.rejection_pct    = (rejected_count / n) * 100.0
        report.timeout_rate     = rejected_count / n   # fraction
        report.partial_fill_rate= partial_count  / n

        # ── Latency ───────────────────────────────────────────────────────────
        if latencies_ms:
            report.avg_latency_ms = sum(latencies_ms) / len(latencies_ms)
            sorted_lat = sorted(latencies_ms)
            p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
            report.p95_latency_ms = sorted_lat[p95_idx]

        # ── Maker percentage ──────────────────────────────────────────────────
        report.maker_pct = (maker_count / n) * 100.0

        # ── Execution stability score ─────────────────────────────────────────
        rejection_rate = rejected_count / n
        timeout_rate   = timeout_count  / n
        norm_slippage  = report.avg_slippage_bps / 50.0  # 50 bps = "bad"
        stability = 1.0 - (
            rejection_rate * 0.3
            + timeout_rate  * 0.4
            + norm_slippage * 0.3
        )
        report.execution_stability_score = max(0.0, min(1.0, stability))

        # ── Order-type quality ────────────────────────────────────────────────
        def _order_quality(slippage_list: List[float]) -> float:
            if not slippage_list:
                return 1.0
            avg = sum(slippage_list) / len(slippage_list)
            # normalise: 0 bps → 1.0, 100 bps → 0.0
            return max(0.0, min(1.0, 1.0 - avg / 100.0))

        report.stop_execution_quality = _order_quality(sl_slippages)
        report.tp_execution_quality   = _order_quality(tp_slippages)

        # ── Finalize breakdowns ───────────────────────────────────────────────
        for strat, d in by_strategy.items():
            cnt = d["count"]
            d["avg_slippage_bps"]  = d["total_slippage_bps"] / cnt if cnt else 0.0
            d["avg_fill_efficiency"]= d["fill_efficiency_sum"] / cnt if cnt else 1.0
            d.pop("total_slippage_bps", None)
            d.pop("fill_efficiency_sum", None)

        for sym, d in by_symbol.items():
            cnt = d["count"]
            d["avg_slippage_bps"]  = d["total_slippage_bps"] / cnt if cnt else 0.0
            d["avg_fill_efficiency"]= d["fill_efficiency_sum"] / cnt if cnt else 1.0
            d.pop("total_slippage_bps", None)
            d.pop("fill_efficiency_sum", None)

        report.by_strategy = dict(by_strategy)
        report.by_symbol   = dict(by_symbol)

        return report

    # ── I/O ───────────────────────────────────────────────────────────────────

    def load_from_file(self, path: str | Path) -> int:
        """Load records from a trade_outcomes.jsonl file.

        Returns the number of records successfully loaded.
        Existing in-memory records are retained; loaded records are appended.
        """
        path = Path(path)
        if not path.exists():
            logger.warning("load_from_file: %s does not exist", path)
            return 0

        loaded = 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        outcome = json.loads(line)
                        result  = self.record_from_outcome(outcome)
                        if result is not None:
                            loaded += 1
                    except json.JSONDecodeError as exc:
                        logger.debug(
                            "load_from_file: skip line %d — JSON error: %s", lineno, exc
                        )
        except OSError as exc:
            logger.error("load_from_file: cannot read %s — %s", path, exc)

        logger.info("load_from_file: loaded %d records from %s", loaded, path)
        return loaded

    def persist_report(self, path: str | Path) -> None:
        """Write the current analytics report as an atomic JSON file.

        Uses a temp file + os.replace to guarantee atomicity.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        report = self.generate_report()
        payload = asdict(report)

        try:
            fd, tmp_path = tempfile.mkstemp(
                dir    = path.parent,
                prefix = ".tmp_exec_report_",
                suffix = ".json",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2, default=str)
                os.replace(tmp_path, path)
                logger.info("persist_report: wrote %s", path)
            except Exception:  # noqa: BLE001
                os.unlink(tmp_path)
                raise
        except OSError as exc:
            logger.error("persist_report: failed to write %s — %s", path, exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_derived(rec: ExecutionRecord) -> ExecutionRecord:
        """Return a copy of *rec* with slippage_bps filled in if missing."""
        if rec.slippage_bps == 0.0 and rec.expected_price > 0 and rec.actual_price > 0:
            if rec.side == "long":
                # Paid more than expected → positive slippage cost
                slip = (
                    abs(rec.actual_price - rec.expected_price)
                    / rec.expected_price
                    * 10_000
                )
            else:
                # Short: sold lower than expected → positive slippage cost
                slip = (
                    abs(rec.expected_price - rec.actual_price)
                    / rec.expected_price
                    * 10_000
                )
            # Dataclasses are mutable; mutate and return
            rec.slippage_bps = slip

        # Fill partial flag consistency
        if rec.expected_qty > 0 and rec.actual_qty < rec.expected_qty * 0.999:
            rec.partial_fill = True
            rec.partial_fill_pct = (rec.actual_qty / rec.expected_qty) * 100.0

        return rec

    def _persist_record(self, rec: ExecutionRecord) -> None:
        """Append one JSON line to data/execution_analytics.jsonl (fcntl-locked)."""
        payload = json.dumps(asdict(rec), default=str)
        try:
            with _ANALYTICS_JSONL.open("a", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                try:
                    fh.write(payload + "\n")
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning("_persist_record: could not write to %s — %s",
                           _ANALYTICS_JSONL, exc)

    def _emit_metrics(self, rec: ExecutionRecord) -> None:
        """Push per-record Prometheus metrics if registry is available."""
        if _metrics is None:
            return
        try:
            if rec.rejected:
                _metrics.record_exchange_error("order_rejected")
            if rec.timed_out:
                _metrics.record_exchange_error("order_timeout")
        except Exception as exc:  # noqa: BLE001
            logger.debug("_emit_metrics error: %s", exc)
