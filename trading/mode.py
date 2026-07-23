"""Trading mode management — DEMO vs LIVE.

DEMO: signals are evaluated and logged but no real orders are placed.
LIVE: signals trigger real orders on Crypto.com.

Mode persists across restarts in data/trading_mode.json.
"""
from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR  = Path(__file__).parent.parent / "data"
_MODE_FILE = _DATA_DIR / "trading_mode.json"

DEFAULT_MODE = "DEMO"


def get_mode() -> str:
    """Return current trading mode: 'DEMO' or 'LIVE'.

    Falls back to DEFAULT_MODE (DEMO) for a missing file, unparseable JSON,
    or any value other than the two known modes — a corrupted or hand-edited
    mode file must never be interpreted as LIVE.
    """
    if _MODE_FILE.exists():
        try:
            mode = json.loads(_MODE_FILE.read_text(encoding="utf-8")).get("mode", DEFAULT_MODE)
            if mode in ("DEMO", "LIVE"):
                return mode
        except Exception:
            pass
    return DEFAULT_MODE


def set_mode(mode: str) -> None:
    """Persist trading mode. Raises ValueError for unknown modes."""
    if mode not in ("DEMO", "LIVE"):
        raise ValueError(f"Invalid mode: {mode}. Must be DEMO or LIVE.")
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _MODE_FILE.write_text(json.dumps({"mode": mode}, indent=2), encoding="utf-8")


def is_live() -> bool:
    """Return True when the bot is in LIVE trading mode."""
    return get_mode() == "LIVE"
