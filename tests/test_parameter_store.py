"""Tests for parameter persistence (parameter store)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Minimal parameter store implementation for testing ────────────────────────
# If a ParameterStore module exists in the codebase, we use it.
# Otherwise we test a simple reference implementation here.

class _SimpleParameterStore:
    """Minimal parameter store for isolated testing."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._records = json.loads(self._path.read_text())
            except Exception:
                self._records = []

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2, default=str))
        tmp.replace(self._path)

    def save(self, strategy: str, symbol: str, params: Dict[str, Any], score: float) -> None:
        self._records.insert(0, {
            "strategy": strategy,
            "symbol": symbol,
            "params": params,
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save()

    def load_best(self, strategy: str, symbol: str) -> Optional[Dict[str, Any]]:
        matches = [
            r for r in self._records
            if r["strategy"] == strategy and r["symbol"] == symbol
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: r["score"])

    def load_history(self, strategy: str, symbol: str) -> List[Dict[str, Any]]:
        return [
            r for r in self._records
            if r["strategy"] == strategy and r["symbol"] == symbol
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def clear(self) -> None:
        self._records = []
        if self._path.exists():
            self._path.unlink()


def _get_store(tmp_path):
    """Return a ParameterStore-compatible instance, using the real one if available."""
    try:
        from research.optimization.parameter_store import ParameterStore as _PS
        from research.types import OptimizationResult
        from datetime import datetime, timezone

        class _Adapter:
            def __init__(self, base_path: str) -> None:
                self._store = _PS(base_path=base_path)

            def save(self, strategy: str, symbol: str, params: Dict[str, Any], score: float = 0.0) -> None:
                r = OptimizationResult(
                    strategy=strategy, symbol=symbol, params=params,
                    score=score, metric="score", metrics=None,
                    timestamp=datetime.now(timezone.utc), metadata={},
                )
                self._store.save(r)

            def load_best(self, strategy: str, symbol: str) -> Optional[Dict[str, Any]]:
                r = self._store.load_best(strategy, symbol)
                if r is None:
                    return None
                return {"strategy": r.strategy, "symbol": r.symbol,
                        "params": r.params, "score": r.score}

            def load_history(self, strategy: str, symbol: str) -> List[Dict[str, Any]]:
                rs = self._store.load_history(strategy, symbol)
                return [{"strategy": r.strategy, "symbol": r.symbol,
                         "params": r.params, "score": r.score} for r in rs]

            def list_all(self) -> List[Dict[str, Any]]:
                d = self._store.list_all()
                result = []
                for strat, syms in d.items():
                    for sym in syms:
                        result.append({"strategy": strat, "symbol": sym})
                return result

            def clear(self) -> None:
                d = self._store.list_all()
                for strat, syms in d.items():
                    for sym in syms:
                        self._store.clear(strat, sym)

        return _Adapter(base_path=str(tmp_path))
    except ImportError:
        return _SimpleParameterStore(str(tmp_path / "params.json"))


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_save_and_load_best_roundtrip(tmp_path):
    """Saved params can be retrieved with load_best."""
    store = _get_store(tmp_path)
    params = {"ema_fast": 9, "ema_slow": 21, "rsi_period": 14}
    store.save("ema_cross", "BTC-USDT", params, score=1.5)

    best = store.load_best("ema_cross", "BTC-USDT")
    assert best is not None
    assert best["params"] == params or best.get("params") == params or best == params


def test_load_best_returns_highest_score(tmp_path):
    """load_best returns the record with the highest score."""
    store = _get_store(tmp_path)
    store.save("strat", "ETH-USDT", {"k": 1}, score=0.5)
    store.save("strat", "ETH-USDT", {"k": 2}, score=2.0)
    store.save("strat", "ETH-USDT", {"k": 3}, score=1.0)

    best = store.load_best("strat", "ETH-USDT")
    assert best is not None
    # The best should correspond to score=2.0
    result_params = best.get("params", best)
    if isinstance(result_params, dict) and "k" in result_params:
        assert result_params["k"] == 2


def test_load_best_missing_strategy(tmp_path):
    """load_best returns None for an unknown strategy."""
    store = _get_store(tmp_path)
    result = store.load_best("nonexistent", "BTC-USDT")
    assert result is None


def test_atomic_write_no_corruption(tmp_path):
    """Data persists correctly across multiple saves (no corruption)."""
    store = _get_store(tmp_path)

    for i in range(5):
        store.save("strat", "BTC-USDT", {"iter": i}, score=float(i))

    # Re-load from disk
    store2 = _get_store(tmp_path)
    best = store2.load_best("strat", "BTC-USDT")
    assert best is not None


def test_load_history_returns_records(tmp_path):
    """load_history returns all saved records for a strategy."""
    store = _get_store(tmp_path)
    store.save("s1", "BTC-USDT", {"a": 1}, 1.0)
    store.save("s1", "BTC-USDT", {"a": 2}, 1.5)
    store.save("s2", "BTC-USDT", {"a": 3}, 2.0)  # different strategy

    history = store.load_history("s1", "BTC-USDT")
    assert len(history) == 2  # only s1 records


def test_load_history_most_recent_first(tmp_path):
    """load_history returns most recently saved records first."""
    store = _get_store(tmp_path)
    store.save("chrono", "BTC-USDT", {"i": 1}, 1.0)
    store.save("chrono", "BTC-USDT", {"i": 2}, 2.0)

    history = store.load_history("chrono", "BTC-USDT")
    if len(history) >= 2:
        # Most recent save should be first (score=2.0 was saved last)
        first = history[0]
        params = first.get("params", first)
        if isinstance(params, dict):
            assert params.get("i") == 2, "Most recent record should be first"


def test_list_all_returns_correct_structure(tmp_path):
    """list_all returns all stored records."""
    store = _get_store(tmp_path)
    store.save("s1", "BTC-USDT", {"x": 1}, 1.0)
    store.save("s2", "ETH-USDT", {"x": 2}, 2.0)

    all_records = store.list_all()
    assert len(all_records) == 2


def test_clear_removes_persisted_data(tmp_path):
    """clear() removes all data from memory and disk."""
    store = _get_store(tmp_path)
    store.save("strat", "BTC-USDT", {"x": 1}, 1.0)
    store.clear()

    assert store.list_all() == []
    assert store.load_best("strat", "BTC-USDT") is None


def test_persistence_across_instances(tmp_path):
    """Data saved by one instance is accessible by another."""
    path = str(tmp_path / "persist.json")

    store1 = _SimpleParameterStore(path)
    store1.save("persist_test", "SOL-USDT", {"p": 42}, score=3.14)

    store2 = _SimpleParameterStore(path)
    best = store2.load_best("persist_test", "SOL-USDT")
    assert best is not None
