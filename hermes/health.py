"""Hermes — on-disk health inspection for every overseen project.

Pure functions that read runtime artifacts to report each bot's health WITHOUT
importing its heavy runtime (no telegram, no playwright, no ollama, no exchange
clients). Every helper tolerates missing/corrupt files and returns sensible
"unknown"/"idle" defaults — nothing in here should ever raise.

The ClawBot heuristics mirror dashboard/app.py so the two stay consistent:
freshness of data/conversation_history.json (or usage_stats.json) → running/idle
+ last_seen, recent trades from data/logs/trades.log, and TJR setups from
data/logs/tjr_setups.jsonl when present. Ollama is optional and best-effort.

HaulYeah is inspected via trash_hauling_bot/data/: audit.log freshness,
pending_outreach.json (the local outreach queue), and an optional leads store.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT             = Path(__file__).parent.parent
DATA_DIR         = ROOT / "data"
HAULYEAH_DIR     = ROOT / "trash_hauling_bot"
HAULYEAH_DATA    = HAULYEAH_DIR / "data"

# A bot is considered "running" if its sentinel file changed within this window.
_RUNNING_WINDOW_S = 300          # 5 min — matches dashboard/app.py
_IDLE_ALERT_S     = 6 * 60 * 60  # 6h — surfaced as an alert in briefing.py


# ── Generic helpers ───────────────────────────────────────────────────────────

def _read_json(path: Path, default):
    """Read JSON from path, returning default on any error (missing/corrupt)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _age_seconds(path: Path):
    """Seconds since path was last modified, or None if it doesn't exist."""
    try:
        return _now_ts() - path.stat().st_mtime
    except Exception:
        return None


def _humanize_age(age):
    """Render an age in seconds as a compact 'last seen' label."""
    if age is None:
        return "never"
    age = max(0, int(age))
    if age < _RUNNING_WINDOW_S:
        return f"{age}s ago"
    mins = age // 60
    hrs  = mins // 60
    if hrs:
        return f"{hrs}h {mins % 60}m ago"
    return f"{mins}m ago"


def _liveness(sentinel_age):
    """Map a sentinel file age to (running, status, last_seen)."""
    if sentinel_age is None:
        return False, "unknown", "never"
    running = sentinel_age < _RUNNING_WINDOW_S
    status  = "running" if running else "idle"
    return running, status, _humanize_age(sentinel_age)


# ── ClawBot ───────────────────────────────────────────────────────────────────

def _clawbot_recent_trades(n: int = 5, data_dir: Path = DATA_DIR) -> list:
    """Last n parsed TRADE_DECISION entries from data/logs/trades.log."""
    log = data_dir / "logs" / "trades.log"
    if not log.exists():
        return []
    try:
        lines = [l for l in log.read_text(encoding="utf-8").splitlines()
                 if "TRADE_DECISION" in l][-n:]
    except Exception:
        return []
    out = []
    for raw in lines:
        parts = raw.split(" | ")
        ts = parts[1].replace("T", " ")[:16] if len(parts) > 1 else ""
        entry = {"ts": ts, "action": "?", "coin": "?", "status": "?"}
        if len(parts) > 2:
            try:
                data = json.loads(parts[2])
                entry["action"] = data.get("action", "?")
                entry["coin"]   = data.get("coin", "?")
                entry["status"] = data.get("status", "?")
            except Exception:
                entry["status"] = parts[2][:60]
        out.append(entry)
    return out


def _clawbot_tjr_setups(n: int = 5, data_dir: Path = DATA_DIR) -> list:
    """Last n TJR setups from data/logs/tjr_setups.jsonl (one JSON obj per line).

    The file is optional — degrade to an empty list if absent or unreadable.
    """
    path = data_dir / "logs" / "tjr_setups.jsonl"
    if not path.exists():
        return []
    try:
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()][-n:]
    except Exception:
        return []
    out = []
    for raw in lines:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


def get_ollama_status() -> dict:
    """Optional, best-effort Ollama probe. Never raises; offline if unavailable."""
    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
        cfg    = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
        return {
            "online": True,
            "models": models,
            "active": cfg if cfg in models else (models[0] if models else "none"),
        }
    except Exception:
        return {"online": False, "models": [], "active": "offline"}


def get_clawbot_health(data_dir: Path = DATA_DIR) -> dict:
    """Structured health for the ClawBot crypto trader.

    Liveness mirrors dashboard/app.py: freshness of conversation_history.json,
    falling back to usage_stats.json. Adds recent trades + TJR setups.
    """
    data_dir = Path(data_dir)
    hist  = data_dir / "conversation_history.json"
    stats = data_dir / "usage_stats.json"
    sentinel = hist if hist.exists() else (stats if stats.exists() else None)
    sentinel_age = _age_seconds(sentinel) if sentinel is not None else None
    running, status, last_seen = _liveness(sentinel_age)

    return {
        "name":        "ClawBot",
        "running":     running,
        "status":      status,
        "last_seen":   last_seen,
        "age_seconds": sentinel_age,
        "ollama":      get_ollama_status(),
        "recent_trades": _clawbot_recent_trades(data_dir=data_dir),
        "tjr_setups":  _clawbot_tjr_setups(data_dir=data_dir),
    }


# ── HaulYeah ──────────────────────────────────────────────────────────────────

def _haulyeah_pending_outreach(data_dir: Path = HAULYEAH_DATA) -> list:
    """The local outreach queue (pending_outreach.json). [] if missing/corrupt.

    Tolerates either a bare list or a {"pending": [...]} wrapper.
    """
    raw = _read_json(Path(data_dir) / "pending_outreach.json", [])
    if isinstance(raw, dict):
        raw = raw.get("pending", raw.get("leads", []))
    return raw if isinstance(raw, list) else []


def _haulyeah_leads(data_dir: Path = HAULYEAH_DATA) -> list:
    """Optional local leads store (leads.json). [] if absent — HaulYeah usually
    keeps leads in Google Sheets, so this is a graceful best-effort read."""
    raw = _read_json(Path(data_dir) / "leads.json", [])
    if isinstance(raw, dict):
        raw = raw.get("leads", [])
    return raw if isinstance(raw, list) else []


def get_haulyeah_health(data_dir: Path = HAULYEAH_DATA) -> dict:
    """Structured health for the HaulYeah lead-gen bot.

    Liveness is derived from audit.log freshness. Adds pending-outreach and
    leads counts. Degrades gracefully when the data dir / files are absent.
    """
    data_dir = Path(data_dir)
    audit = data_dir / "audit.log"
    sentinel_age = _age_seconds(audit)
    running, status, last_seen = _liveness(sentinel_age)

    pending = _haulyeah_pending_outreach(data_dir)
    leads   = _haulyeah_leads(data_dir)

    return {
        "name":            "HaulYeah",
        "running":         running,
        "status":          status,
        "last_seen":       last_seen,
        "age_seconds":     sentinel_age,
        "pending_outreach": len(pending),
        "leads":           len(leads),
    }


# ── Aggregate ─────────────────────────────────────────────────────────────────

def get_all_health() -> dict:
    """Health for every overseen project, keyed by short name.

    Never raises — each project is probed independently so one failing probe
    can't take down the others.
    """
    out = {}
    try:
        out["clawbot"] = get_clawbot_health()
    except Exception as exc:
        out["clawbot"] = {"name": "ClawBot", "running": False, "status": "unknown",
                          "last_seen": "never", "error": str(exc)[:80]}
    try:
        out["haulyeah"] = get_haulyeah_health()
    except Exception as exc:
        out["haulyeah"] = {"name": "HaulYeah", "running": False, "status": "unknown",
                           "last_seen": "never", "error": str(exc)[:80]}
    return out
