"""
dashboard/api/server.py — OpenClaw HQ status dashboard (FastAPI).

A READ-ONLY, side-effect-free status surface for the OpenClaw ecosystem,
served for phone/LAN access on port 8000. It NEVER imports trading/execution
code, NEVER reads secret values, and NEVER places orders — it only inspects
file presence and log freshness on disk.

Endpoints:
    GET  /            -> static/index.html (the dashboard)
    GET  /signals     -> static/signals.html (paper-watch signal table)
    GET  /qr.png      -> static/qr.png (share QR, written by dashboard/start.py)
    GET  /status      -> JSON: live status of every component + paper-watch summary
    GET  /paper-watch -> JSON: last 50 liquidity-sweep signals
    WS   /ws          -> pushes the /status payload every 10s

Run:
    .venv\\Scripts\\python.exe -m uvicorn dashboard.api.server:app --host 0.0.0.0 --port 8000
    (or use dashboard/start.py for the LAN URL + QR launcher)
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

# ---------------------------------------------------------------------------
# Paths — server.py lives at <repo>/dashboard/api/server.py
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PAPER_WATCH_FILE = REPO_ROOT / "data" / "paper_watch" / "liquidity_sweep.jsonl"

# Version: repo docs disagree (0.3 in build spec, 0.8/0.9 in CLAUDE.md). Default
# to the latest documented value; override with OPENCLAW_VERSION if desired.
OPENCLAW_VERSION = os.getenv("OPENCLAW_VERSION", "0.9")

# A log written within this many hours => the component is "fresh" / alive.
FRESH_HOURS = 24

app = FastAPI(title="OpenClaw HQ", version=OPENCLAW_VERSION)


# ---------------------------------------------------------------------------
# Small filesystem helpers (no imports of project runtime modules)
# ---------------------------------------------------------------------------
def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _newest_mtime(*paths: Path) -> float | None:
    """Return the newest mtime among the given existing paths, else None."""
    mtimes = [p.stat().st_mtime for p in paths if p.exists() and p.is_file()]
    return max(mtimes) if mtimes else None


def _hours_since(ts: float | None) -> float | None:
    if ts is None:
        return None
    return (datetime.now(tz=timezone.utc).timestamp() - ts) / 3600.0


def _freshness_status(mtime: float | None, *, present: bool) -> str:
    """ready (fresh log) / attention (stale or no log) / broken (missing)."""
    if not present:
        return "broken"
    hours = _hours_since(mtime)
    if hours is None:
        return "attention"  # present but no activity log to confirm liveness
    return "ready" if hours <= FRESH_HOURS else "attention"


def _component_openalice() -> dict:
    base = REPO_ROOT / "OpenAlice"
    dist = base / "dist" / "index.js"
    exec_log = base / "data" / "logs" / "execution.log"
    paper_log = base / "data" / "logs" / "paper.log"
    present = base.exists() and dist.exists()
    mtime = _newest_mtime(exec_log, paper_log)
    return {
        "status": _freshness_status(mtime, present=present),
        "last_seen": _iso(mtime),
        "note": "paper-only (MockBroker); no live until regime gate"
        if present
        else "OpenAlice build (dist/) missing",
    }


def _component_clawbot() -> dict:
    entry = REPO_ROOT / "start.py"
    strategy = REPO_ROOT / "trading" / "strategies" / "liquidity_sweep.py"
    audit_log = REPO_ROOT / "data" / "logs" / "audit.log"
    cmd_audit = REPO_ROOT / "data" / "logs" / "command_audit.log"
    present = entry.exists() and strategy.exists()
    mtime = _newest_mtime(audit_log, cmd_audit)
    return {
        "status": _freshness_status(mtime, present=present),
        "last_seen": _iso(mtime),
        "note": "Telegram bot + Flask dashboard (start.py)"
        if present
        else "start.py or strategy missing",
    }


def _component_content_pipeline() -> dict:
    content = REPO_ROOT / "content"
    files = ["receiver.py", "pipeline.py", "watcher.py", "editor.py",
             "caption_generator.py", "poster.py", "uploader.py"]
    missing = [f for f in files if not (content / f).exists()]
    ig_set = bool(os.getenv("INSTAGRAM_ACCESS_TOKEN"))
    tt_set = bool(os.getenv("TIKTOK_ACCESS_TOKEN"))
    mtime = _newest_mtime(*[content / f for f in files])
    if missing:
        status, note = "broken", f"missing: {', '.join(missing)}"
    elif not (ig_set or tt_set):
        status = "attention"
        note = "code ready; IG/TikTok posting creds not set (posting disabled)"
    else:
        status, note = "ready", "code ready; posting creds present"
    return {"status": status, "last_seen": _iso(mtime), "note": note}


def _component_haul() -> dict:
    base = REPO_ROOT / "trash_hauling_bot"
    entry = base / "main.py"
    audit_log = base / "data" / "audit.log"
    present = base.exists() and entry.exists()
    mtime = _newest_mtime(audit_log)
    return {
        "status": _freshness_status(mtime, present=present),
        "last_seen": _iso(mtime),
        "note": "HaulYeah lead-gen bot (separate venture; read-only here)"
        if present
        else "trash_hauling_bot/main.py missing",
    }


def _paper_watch_summary() -> dict:
    total = 0
    last_signal = None
    if PAPER_WATCH_FILE.exists():
        last_line = None
        with PAPER_WATCH_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    total += 1
                    last_line = line
        if last_line:
            try:
                last_signal = json.loads(last_line).get("ts")
            except json.JSONDecodeError:
                last_signal = None
    return {"total_signals": total, "last_signal": last_signal}


def build_status() -> dict:
    """Assemble the full status payload. Pure reads — safe to call anytime."""
    return {
        "openclaw_version": OPENCLAW_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "components": {
            "openalice": _component_openalice(),
            "clawbot": _component_clawbot(),
            "content_pipeline": _component_content_pipeline(),
            "haul": _component_haul(),
        },
        "paper_watch": _paper_watch_summary(),
    }


def read_recent_signals(limit: int = 50) -> list[dict]:
    """Return the last `limit` parsed JSONL entries (newest last). Creates the
    file empty if it does not exist."""
    PAPER_WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PAPER_WATCH_FILE.exists():
        PAPER_WATCH_FILE.touch()
        return []
    lines = [ln for ln in PAPER_WATCH_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: list[dict] = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    f = STATIC_DIR / "index.html"
    if not f.exists():
        return PlainTextResponse("index.html not found", status_code=404)
    return FileResponse(f)


@app.get("/signals")
def signals_page():
    f = STATIC_DIR / "signals.html"
    if not f.exists():
        return PlainTextResponse("signals.html not found", status_code=404)
    return FileResponse(f)


@app.get("/qr.png")
def qr_png():
    f = STATIC_DIR / "qr.png"
    if not f.exists():
        return PlainTextResponse("qr.png not generated yet — run dashboard/start.py", status_code=404)
    return FileResponse(f, media_type="image/png")


@app.get("/status")
def status():
    return JSONResponse(build_status())


@app.get("/paper-watch")
def paper_watch():
    return JSONResponse(read_recent_signals(50))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(build_status())
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        return
    except Exception:
        # Client vanished mid-send; close quietly.
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
