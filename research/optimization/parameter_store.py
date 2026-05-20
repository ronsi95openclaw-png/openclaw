"""JSON-backed persistent registry for optimization results."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.types import OptimizationResult, PerformanceMetrics

logger = logging.getLogger(__name__)

# ── JSON serialization helpers ────────────────────────────────────────────────

def _result_to_dict(result: OptimizationResult) -> Dict[str, Any]:
    """Serialize an OptimizationResult to a JSON-safe dict."""
    metrics_dict: Optional[Dict[str, Any]] = None
    if result.metrics is not None:
        metrics_dict = {
            field: getattr(result.metrics, field)
            for field in result.metrics.__dataclass_fields__
        }

    return {
        "strategy": result.strategy,
        "symbol": result.symbol,
        "params": result.params,
        "score": result.score,
        "metric": result.metric,
        "metrics": metrics_dict,
        "timestamp": (
            result.timestamp.isoformat() if result.timestamp is not None else None
        ),
        "metadata": result.metadata,
    }


def _dict_to_result(d: Dict[str, Any]) -> OptimizationResult:
    """Deserialize an OptimizationResult from a JSON dict."""
    metrics: Optional[PerformanceMetrics] = None
    if d.get("metrics") is not None:
        try:
            metrics = PerformanceMetrics(**d["metrics"])
        except (TypeError, KeyError):
            metrics = None

    ts: Optional[datetime] = None
    if d.get("timestamp"):
        try:
            ts = datetime.fromisoformat(d["timestamp"])
        except (ValueError, TypeError):
            ts = None

    return OptimizationResult(
        strategy=d.get("strategy", ""),
        symbol=d.get("symbol", ""),
        params=d.get("params", {}),
        score=float(d.get("score", 0.0)),
        metric=d.get("metric", ""),
        metrics=metrics,
        timestamp=ts,
        metadata=d.get("metadata", {}),
    )


# ── ParameterStore ────────────────────────────────────────────────────────────

class ParameterStore:
    """JSON-backed persistent registry for optimization results.

    Layout on disk::

        {base_path}/{strategy}/{symbol}.json

    Each file contains::

        {
            "best": {...},
            "history": [{...}, ...]
        }

    Writes are atomic (write to a temp file then ``os.replace``).

    Args:
        base_path: Root directory for stored results.
                   Defaults to ``data/optimization``.
    """

    def __init__(self, base_path: str = "data/optimization") -> None:
        self.base_path = Path(base_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self, result: OptimizationResult) -> None:
        """Persist an OptimizationResult.

        Adds the result to history and updates the best result if this one
        scores higher.

        Args:
            result: The result to save.
        """
        path = self._path(result.strategy, result.symbol)
        existing = self._load_raw(path)

        history: List[Dict[str, Any]] = existing.get("history", [])
        history.insert(0, _result_to_dict(result))

        current_best = existing.get("best")
        if (
            current_best is None
            or result.score > float(current_best.get("score", float("-inf")))
        ):
            current_best = _result_to_dict(result)

        self._save_raw(path, {"best": current_best, "history": history})
        logger.debug(
            "ParameterStore: saved %s/%s  score=%.4f",
            result.strategy,
            result.symbol,
            result.score,
        )

    def load_best(self, strategy: str, symbol: str) -> Optional[OptimizationResult]:
        """Load the best known params for strategy + symbol.

        Returns:
            ``OptimizationResult`` or ``None`` if not found.
        """
        path = self._path(strategy, symbol)
        raw = self._load_raw(path)
        best = raw.get("best")
        if best is None:
            return None
        try:
            return _dict_to_result(best)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ParameterStore: failed to deserialize best for %s/%s: %s",
                strategy, symbol, exc,
            )
            return None

    def load_history(
        self, strategy: str, symbol: str, limit: int = 50
    ) -> List[OptimizationResult]:
        """Load optimization history (most recent first).

        Args:
            strategy: Strategy name.
            symbol:   Trading pair symbol.
            limit:    Maximum number of records to return.

        Returns:
            List of ``OptimizationResult`` objects, newest first.
        """
        path = self._path(strategy, symbol)
        raw = self._load_raw(path)
        history = raw.get("history", [])[:limit]
        results: List[OptimizationResult] = []
        for d in history:
            try:
                results.append(_dict_to_result(d))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ParameterStore: skipping invalid history entry: %s", exc
                )
        return results

    def clear(self, strategy: str, symbol: str) -> None:
        """Delete all stored results for strategy + symbol.

        Args:
            strategy: Strategy name.
            symbol:   Trading pair symbol.
        """
        path = self._path(strategy, symbol)
        if path.exists():
            path.unlink()
            logger.debug("ParameterStore: cleared %s/%s", strategy, symbol)

    def list_all(self) -> Dict[str, List[str]]:
        """Return {strategy: [symbols]} for all stored results.

        Returns:
            Dict mapping strategy name → list of symbol strings.
        """
        output: Dict[str, List[str]] = {}
        if not self.base_path.exists():
            return output

        for strategy_dir in sorted(self.base_path.iterdir()):
            if not strategy_dir.is_dir():
                continue
            symbols: List[str] = []
            for symbol_file in sorted(strategy_dir.iterdir()):
                if symbol_file.suffix == ".json":
                    symbols.append(symbol_file.stem)
            if symbols:
                output[strategy_dir.name] = symbols

        return output

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _path(self, strategy: str, symbol: str) -> Path:
        """Return the file path for a given strategy + symbol."""
        # Sanitise to avoid path traversal
        safe_strategy = _sanitise(strategy)
        safe_symbol = _sanitise(symbol)
        return self.base_path / safe_strategy / f"{safe_symbol}.json"

    def _load_raw(self, path: Path) -> Dict[str, Any]:
        """Load raw JSON dict from path, returning empty dict if missing."""
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("ParameterStore: failed to read %s: %s", path, exc)
            return {}

    def _save_raw(self, path: Path, data: Dict[str, Any]) -> None:
        """Atomically write a JSON dict to path using temp file + rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".tmp_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=_json_default)
            os.replace(tmp_path, str(path))
        except Exception:  # noqa: BLE001
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ── Module-level helpers ──────────────────────────────────────────────────────

def _sanitise(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _json_default(obj: Any) -> Any:
    """JSON serialization fallback for non-standard types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, float):
        import math  # noqa: PLC0415
        if math.isnan(obj) or math.isinf(obj):
            return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
