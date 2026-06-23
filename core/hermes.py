"""Hermes Mission Control — native agent-fleet monitor for OpenClaw.

A self-contained, file-backed re-implementation of the "Hermes mission
control" idea (single pane of glass across many agents) using the existing
OpenClaw stack — no Next.js app, no Postgres. State lives under
``data/hermes/`` so it never collides with the bot's own data files.

Three pieces:
  1. A small JSON-backed registry of agents + a mission backlog.
  2. ``record_heartbeat()`` — agents (ClawBot, HaulYeah, side-agents) POST
     their status here via the dashboard's ``/api/agents/state`` endpoint.
  3. ``hermes_ai_briefing()`` — a short natural-language fleet status written
     by the local brain (``core.brain.ask_hybrid``), cached to spare tokens.

ClawBot is auto-detected from existing data files (usage_stats.json,
conversation_history.json, trades.log) so the panel shows something useful
out of the box, before any heartbeat is ever sent.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
_ROOT        = Path(__file__).parent.parent
_DATA_DIR    = _ROOT / "data"
_HERMES_DIR  = _DATA_DIR / "hermes"
_AGENTS_FILE = _HERMES_DIR / "agents.json"
_MISSION_FILE = _HERMES_DIR / "missions.json"
_BRIEF_FILE  = _HERMES_DIR / "briefing.json"

# ── Tunables ───────────────────────────────────────────────────────────────
ONLINE_WINDOW_S = int(os.getenv("HERMES_ONLINE_WINDOW") or 300)    # < 5m  → online
IDLE_WINDOW_S   = int(os.getenv("HERMES_IDLE_WINDOW") or 1800)     # < 30m → idle
BRIEF_TTL_S     = int(os.getenv("HERMES_BRIEF_TTL") or 300)        # AI briefing cache

_LOCK = threading.Lock()


# ── JSON helpers (atomic write) ────────────────────────────────────────────

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    _HERMES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # atomic on POSIX & Windows


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _ago(epoch: float) -> str:
    """Human 'time since' label."""
    if not epoch:
        return "never"
    secs = int(_now() - epoch)
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h {mins % 60}m ago"
    return f"{hrs // 24}d ago"


def _live_status(reported: str, last_epoch: float) -> str:
    """Derive online/idle/offline from heartbeat age, capped by reported state."""
    age = _now() - last_epoch if last_epoch else 1e12
    if age < ONLINE_WINDOW_S:
        # An agent can self-report a softer state (e.g. "idle") while fresh.
        return reported if reported in {"online", "idle", "busy", "error"} else "online"
    if age < IDLE_WINDOW_S:
        return "error" if reported == "error" else "idle"
    return "offline"


# ── Heartbeat ingestion ────────────────────────────────────────────────────

def record_heartbeat(
    agent_id: str,
    *,
    name: Optional[str] = None,
    status: str = "online",
    current_task: str = "",
    tasks_completed: Optional[int] = None,
    cost_usd: Optional[float] = None,
    meta: Optional[dict] = None,
) -> dict:
    """Upsert one agent's reported state. Returns the stored record.

    Called by the dashboard's POST /api/agents/state endpoint, but also
    importable so an in-process bot can beat directly.
    """
    if not agent_id:
        raise ValueError("agent_id is required")

    with _LOCK:
        agents = _read(_AGENTS_FILE, {})
        rec = agents.get(agent_id, {})
        rec["id"] = agent_id
        rec["name"] = name or rec.get("name") or agent_id
        rec["status"] = status or "online"
        rec["current_task"] = current_task or rec.get("current_task", "")
        if tasks_completed is not None:
            rec["tasks_completed"] = int(tasks_completed)
        if cost_usd is not None:
            rec["cost_usd"] = round(float(cost_usd), 4)
        if meta is not None:
            rec["meta"] = meta
        rec["last_seen_epoch"] = _now()
        rec["last_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec["source"] = "heartbeat"
        agents[agent_id] = rec
        _write(_AGENTS_FILE, agents)
    return rec


# ── Local auto-detection (ClawBot, zero-config) ────────────────────────────

def _detect_clawbot() -> Optional[dict]:
    """Synthesize a ClawBot agent record from existing OpenClaw data files."""
    hist  = _DATA_DIR / "conversation_history.json"
    usage = _DATA_DIR / "usage_stats.json"
    sentinel = hist if hist.exists() else (usage if usage.exists() else None)
    if sentinel is None:
        return None

    last_epoch = sentinel.stat().st_mtime

    # Today's API cost from usage stats (mirrors the dashboard's cost calc).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = _read(usage, {}).get(today, {})
    cost = (stats.get("claude_input_tokens", 0) * 1e-6
            + stats.get("claude_output_tokens", 0) * 5e-6)
    calls = (stats.get("ollama_calls", 0) + stats.get("openrouter_calls", 0)
             + stats.get("claude_calls", 0) + stats.get("cache_hits", 0))

    # Tasks completed today ≈ fired reminders + logged trade decisions.
    fired = sum(1 for t in _read(_DATA_DIR / "tasks.json", [])
                if t.get("status") == "fired")
    trades_log = _DATA_DIR / "logs" / "trades.log"
    trade_count = 0
    if trades_log.exists():
        try:
            trade_count = sum(1 for l in trades_log.read_text(encoding="utf-8").splitlines()
                              if "TRADE_DECISION" in l)
        except Exception:
            pass

    return {
        "id": "clawbot",
        "name": "ClawBot",
        "status": "online",
        "current_task": f"{calls} brain calls today" if calls else "idle",
        "tasks_completed": fired + trade_count,
        "cost_usd": round(cost, 4),
        "meta": {"brain_calls_today": calls},
        "last_seen_epoch": last_epoch,
        "last_seen": datetime.fromtimestamp(last_epoch, timezone.utc).isoformat(timespec="seconds"),
        "source": "local",
    }


def get_agents() -> list[dict]:
    """Return all known agents (heartbeat + auto-detected) with live status.

    Heartbeat records win over auto-detection for the same id.
    """
    merged: dict[str, dict] = {}

    detected = _detect_clawbot()
    if detected:
        merged[detected["id"]] = detected

    for aid, rec in _read(_AGENTS_FILE, {}).items():
        merged[aid] = rec   # heartbeat overrides local detection

    agents = []
    for rec in merged.values():
        last_epoch = rec.get("last_seen_epoch", 0)
        agents.append({
            **rec,
            "live_status": _live_status(rec.get("status", "online"), last_epoch),
            "last_seen_label": _ago(last_epoch),
        })
    # Online first, then most-recently-seen.
    order = {"online": 0, "busy": 1, "error": 2, "idle": 3, "offline": 4}
    agents.sort(key=lambda a: (order.get(a["live_status"], 9), -a.get("last_seen_epoch", 0)))
    return agents


def fleet_summary() -> dict:
    """Aggregate counts and cost across the fleet."""
    agents = get_agents()
    online = sum(1 for a in agents if a["live_status"] in {"online", "busy"})
    return {
        "total": len(agents),
        "online": online,
        "idle": sum(1 for a in agents if a["live_status"] == "idle"),
        "offline": sum(1 for a in agents if a["live_status"] == "offline"),
        "errors": sum(1 for a in agents if a["live_status"] == "error"),
        "tasks_completed": sum(a.get("tasks_completed", 0) for a in agents),
        "cost_usd": round(sum(a.get("cost_usd", 0) for a in agents), 4),
    }


# ── Mission backlog ────────────────────────────────────────────────────────

def get_missions() -> list[dict]:
    missions = _read(_MISSION_FILE, [])
    rank = {"active": 0, "backlog": 1, "done": 2}
    missions.sort(key=lambda m: (rank.get(m.get("status"), 9), m.get("created_at", "")))
    return missions


def add_mission(title: str, agent: str = "", notes: str = "") -> dict:
    if not title.strip():
        raise ValueError("mission title is required")
    with _LOCK:
        missions = _read(_MISSION_FILE, [])
        mission = {
            "id": f"m_{time.time_ns()}",
            "title": title.strip(),
            "agent": agent.strip(),
            "status": "backlog",
            "notes": notes.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        missions.append(mission)
        _write(_MISSION_FILE, missions)
    return mission


def set_mission_status(mission_id: str, status: str) -> bool:
    if status not in {"backlog", "active", "done"}:
        raise ValueError(f"invalid status: {status}")
    with _LOCK:
        missions = _read(_MISSION_FILE, [])
        for m in missions:
            if m["id"] == mission_id:
                m["status"] = status
                _write(_MISSION_FILE, missions)
                return True
    return False


# ── Hermes AI briefing (native local brain) ────────────────────────────────

def hermes_ai_briefing(force: bool = False) -> dict:
    """Short natural-language fleet status from the local brain, cached.

    Returns {"text": str, "generated": iso, "age": "Nm ago"}.
    """
    cached = _read(_BRIEF_FILE, {})
    if (not force and cached.get("ts")
            and _now() - cached["ts"] < BRIEF_TTL_S):
        return {"text": cached["text"], "generated": cached.get("generated", ""),
                "age": _ago(cached["ts"])}

    agents = get_agents()
    summary = fleet_summary()
    missions = [m for m in get_missions() if m["status"] != "done"][:6]

    lines = [f"- {a['name']}: {a['live_status']}, "
             f"{a.get('tasks_completed', 0)} tasks, "
             f"${a.get('cost_usd', 0):.4f}, last seen {a['last_seen_label']}"
             f"{(' — ' + a['current_task']) if a.get('current_task') else ''}"
             for a in agents] or ["- (no agents reporting yet)"]
    mission_lines = [f"- [{m['status']}] {m['title']}"
                     f"{(' (' + m['agent'] + ')') if m.get('agent') else ''}"
                     for m in missions] or ["- (backlog empty)"]

    prompt = (
        "You are Hermes, the mission-control overseer for the OpenClaw agent "
        "fleet. Give the operator a tight status briefing: 2-4 sentences, plain "
        "text, no preamble. Call out anything offline/erroring or any agent "
        "burning cost, then the top thing to focus on next.\n\n"
        f"FLEET: {summary['online']}/{summary['total']} online, "
        f"{summary['offline']} offline, {summary['errors']} errored, "
        f"{summary['tasks_completed']} tasks done today, "
        f"${summary['cost_usd']:.4f} spent.\n\n"
        "AGENTS:\n" + "\n".join(lines) + "\n\n"
        "OPEN MISSIONS:\n" + "\n".join(mission_lines)
    )

    try:
        from core.brain import ask_hybrid
        text, _ = ask_hybrid(prompt, force="simple")
    except Exception as exc:
        # Brain unavailable — fall back to a deterministic summary.
        text = (f"{summary['online']}/{summary['total']} agents online, "
                f"{summary['offline']} offline, {summary['errors']} errored. "
                f"{summary['tasks_completed']} tasks done today, "
                f"${summary['cost_usd']:.4f} spent. "
                f"(AI briefing offline: {str(exc)[:80]})")

    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(_BRIEF_FILE, {"text": text, "ts": _now(), "generated": generated})
    return {"text": text, "generated": generated, "age": "just now"}
