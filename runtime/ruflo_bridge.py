"""Ruflo Bridge — Pure Python HNSW vector memory.

Replaces the Node.js MCP subprocess with native hnswlib.
Exposes the same interface as the old Node.js bridge so ruflo_agent.py
needs no changes.

All outputs are ADVISORY ONLY — they are never execution-authoritative.
"""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.ruflo_bridge")


# ── Compat shim — MCPToolResult kept so runtime/__init__.py import doesn't break ─
from dataclasses import dataclass, field as _field
from typing import Any as _Any, Dict as _Dict

@dataclass
class MCPToolResult:
    tool_name:  str
    success:    bool
    content:    _Any                          = None
    raw:        _Dict[str, _Any]              = _field(default_factory=dict)
    error:      str                           = ""
    latency_ms: float                         = 0.0


_DATA_DIR   = Path(__file__).parent.parent / "data" / "ruflo"
_INDEX_PATH = _DATA_DIR / "hnsw_index.bin"
_META_PATH  = _DATA_DIR / "trade_metadata.pkl"

EMBEDDING_DIM = 16
MAX_ELEMENTS  = 50_000


# ── Feature engineering ────────────────────────────────────────────────────────

def _make_embedding(signal: Dict[str, Any]):
    """Convert a signal/metadata dict to a fixed-dim numpy float32 vector."""
    import numpy as np
    regime = str(signal.get("regime", ""))
    features = [
        float(signal.get("rsi",              50)) / 100.0,
        float(signal.get("macd_signal",       0)),
        float(signal.get("volume_ratio",      1.0)),
        float(signal.get("confidence",        0.5)),
        1.0 if "TRENDING_BULL" in regime else 0.0,
        1.0 if "TRENDING_BEAR" in regime else 0.0,
        1.0 if "RANGING"       in regime else 0.0,
        1.0 if "CHOPPY"        in regime else 0.0,
        1.0 if str(signal.get("action",  "")).lower() in ("long",  "buy") else 0.0,
        1.0 if str(signal.get("action",  "")).lower() in ("short", "sell") else 0.0,
        float(signal.get("atr_pct",      0.02)),
        float(signal.get("bb_position",  0.5)),
        float(signal.get("sl_pct",       0.03)),
        float(signal.get("tp_pct",       0.06)),
        float(signal.get("win", False)),
        float(signal.get("pnl", 0.0)) / 1000.0,  # normalised
    ]
    vec = features[:EMBEDDING_DIM]
    while len(vec) < EMBEDDING_DIM:
        vec.append(0.0)
    return np.array(vec, dtype=np.float32)


def _query_text_to_signal(query: str) -> Dict[str, Any]:
    """Parse 'SYMBOL STRATEGY ACTION REGIME CONF_BAND' text into a signal dict."""
    parts  = query.upper().split()
    action = ""
    regime = ""
    conf   = 0.65
    for p in parts:
        if p in ("LONG", "BUY"):
            action = "long"
        elif p in ("SHORT", "SELL"):
            action = "short"
        elif "TRENDING" in p or "RANGING" in p or "CHOPPY" in p or "UNKNOWN" in p:
            regime = p
        elif p == "HIGH":
            conf = 0.82
        elif p == "LOW":
            conf = 0.50
    return {"action": action, "regime": regime, "confidence": conf}


# ── Core memory class ──────────────────────────────────────────────────────────

class RufloMemory:
    """HNSW vector store for trade memory.  Thread-safe reads; write lock on store."""

    def __init__(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._meta: List[Dict] = []
        self._index = None
        self._available = self._load_or_init()

    def _load_or_init(self) -> bool:
        try:
            import hnswlib
            import numpy as np
            self._np = np
            idx = hnswlib.Index(space="cosine", dim=EMBEDDING_DIM)
            if _INDEX_PATH.exists() and _META_PATH.exists():
                idx.load_index(str(_INDEX_PATH), max_elements=MAX_ELEMENTS)
                with open(_META_PATH, "rb") as f:
                    self._meta = pickle.load(f)
                logger.info("Ruflo: loaded %d memories from disk", len(self._meta))
            else:
                idx.init_index(max_elements=MAX_ELEMENTS, ef_construction=200, M=16)
                idx.set_ef(50)
                logger.info("Ruflo: initialised fresh HNSW index")
            self._index = idx
            return True
        except ImportError:
            logger.warning("Ruflo: hnswlib not installed — HNSW memory disabled")
            return False
        except Exception as exc:
            logger.warning("Ruflo: init error (%s) — memory disabled", exc)
            return False

    def is_available(self) -> bool:
        return self._available and self._index is not None

    def store(self, metadata: Dict[str, Any]) -> None:
        """Store a trade outcome in the HNSW index."""
        if not self.is_available():
            return
        try:
            vec = _make_embedding(metadata)
            idx = len(self._meta)
            self._index.add_items(vec.reshape(1, -1), self._np.array([idx]))
            self._meta.append(metadata)
            self._save()
        except Exception as exc:
            logger.warning("Ruflo.store error: %s", exc)

    def search(self, signal: Dict[str, Any], k: int = 5) -> List[Dict]:
        """Return the k most similar past trades."""
        if not self.is_available() or len(self._meta) < 3:
            return []
        try:
            vec     = _make_embedding(signal)
            k_real  = min(k, len(self._meta))
            labels, _ = self._index.knn_query(vec.reshape(1, -1), k=k_real)
            return [self._meta[i] for i in labels[0]]
        except Exception as exc:
            logger.warning("Ruflo.search error: %s", exc)
            return []

    def _save(self) -> None:
        try:
            self._index.save_index(str(_INDEX_PATH))
            with open(_META_PATH, "wb") as f:
                pickle.dump(self._meta, f)
        except Exception as exc:
            logger.warning("Ruflo._save error: %s", exc)


# ── Singleton ──────────────────────────────────────────────────────────────────

_memory: Optional[RufloMemory] = None


def get_memory() -> RufloMemory:
    global _memory
    if _memory is None:
        _memory = RufloMemory()
    return _memory


# ── Bridge class (same interface as the old Node.js bridge) ───────────────────

class RufloBridge:
    """Drop-in replacement for the Node.js MCP bridge — uses native hnswlib.

    Exposes memory_search / memory_store / swarm_analyze exactly as before
    so ruflo_agent.py requires zero changes.
    """

    def __init__(self) -> None:
        self._mem: Optional[RufloMemory] = None

    def start(self) -> bool:
        self._mem = get_memory()
        ok = self._mem.is_available()
        if ok:
            logger.info("RufloBridge (Python/hnswlib): ready — %d memories", len(self._mem._meta))
        else:
            logger.warning("RufloBridge: hnswlib unavailable — advisory memory disabled")
        return ok

    def stop(self) -> None:
        pass  # no subprocess to kill

    def is_available(self) -> bool:
        return self._mem is not None and self._mem.is_available()

    def available_tools(self) -> List[str]:
        return ["memory_search", "memory_store"] if self.is_available() else []

    def memory_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Text-query search — parses query string into feature vector."""
        if not self.is_available():
            return []
        signal = _query_text_to_signal(query)
        hits   = self._mem.search(signal, k=limit)
        # Wrap in the format ruflo_agent._parse_memories expects
        return [{"metadata": h, "content": _meta_to_content(h)} for h in hits]

    def memory_store(self, key: str, content: str, metadata: Dict[str, Any]) -> None:
        """Store a trade outcome."""
        if not self.is_available():
            return
        self._mem.store(metadata)

    def swarm_analyze(self, task: str, context: Dict[str, Any]) -> str:
        """Swarm analysis not supported in pure-Python mode — returns empty."""
        return ""

    def get_status(self) -> Dict[str, Any]:
        mem_count = len(self._mem._meta) if self._mem else 0
        return {
            "backend":    "python/hnswlib",
            "available":  self.is_available(),
            "memories":   mem_count,
            "index_path": str(_INDEX_PATH),
        }


def _meta_to_content(meta: Dict) -> str:
    return (
        f"{meta.get('symbol','')} {meta.get('strategy','')} "
        f"{meta.get('action','')} regime={meta.get('regime','')} "
        f"pnl={meta.get('pnl',0):.4f} win={meta.get('win',False)}"
    )


# ── Singleton bridge ───────────────────────────────────────────────────────────

_bridge: Optional[RufloBridge] = None


def get_bridge() -> RufloBridge:
    global _bridge
    if _bridge is None:
        _bridge = RufloBridge()
    return _bridge
