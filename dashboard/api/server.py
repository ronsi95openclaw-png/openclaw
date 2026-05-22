"""OpenClaw Dashboard API Server.

Architecture:
    CryptoComBot (sync thread)
        ↓  publish() via EventBus
    FastAPI event loop
        ↓  WebSocket broadcast
    React/Next.js Control Center

Endpoints:
    GET  /api/status        — full bot snapshot
    GET  /api/positions     — open positions
    GET  /api/trades        — recent trade log (last 50)
    GET  /api/weights       — strategy weights + stats
    GET  /api/health        — capability matrix
    GET  /api/analysis      — latest Claude Opus report
    POST /api/bot/start     — start the bot
    POST /api/bot/stop      — stop the bot
    POST /api/bot/configure — update settings (risk_pct, demo_mode)
    WS   /ws                — real-time event stream
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard.api.event_bus import get_bus

logger = logging.getLogger("openclaw.dashboard.server")

app = FastAPI(title="OpenClaw Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Bot singleton ─────────────────────────────────────────────────────────────

_bot = None


def get_bot():
    global _bot
    if _bot is None:
        from trading.cryptocom_bot import CryptoComBot
        _bot = CryptoComBot()
    return _bot


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    bus = get_bus()
    bus.set_loop(asyncio.get_event_loop())
    asyncio.create_task(_poll_bot_state())
    logger.info("Dashboard API started — WebSocket event bus active")


async def _poll_bot_state():
    """Poll bot status every second; publish to event bus on change."""
    last_hash: Optional[int] = None
    while True:
        await asyncio.sleep(1)
        try:
            bot    = get_bot()
            status = bot.get_status()
            h      = hash(json.dumps(status, sort_keys=True, default=str))
            if h != last_hash:
                last_hash = h
                get_bus().publish("state_update", status)
        except Exception as exc:
            logger.debug("Poll error: %s", exc)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    return get_bot().get_status()


@app.get("/api/positions")
def api_positions():
    return get_bot().state.open_positions


@app.get("/api/trades")
def api_trades(limit: int = 50):
    limit = max(1, min(limit, 500))
    return get_bot().state.trade_log[:limit]


@app.get("/api/weights")
def api_weights():
    return get_bot().weights.summary()


@app.get("/api/health")
def api_health():
    from runtime.capability_matrix import assess
    return assess()


@app.get("/api/analysis")
def api_analysis():
    analysis_dir = Path(__file__).parent.parent.parent / "data" / "optimization"
    files = sorted(analysis_dir.glob("analysis_*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return {"error": "No analysis yet — run the bot overnight or trigger manually"}
    try:
        return json.loads(files[0].read_text())
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/outcomes/latest")
def api_outcomes(limit: int = 20):
    path = Path(__file__).parent.parent.parent / "data" / "logs" / "trade_outcomes.jsonl"
    if not path.exists():
        return []
    records = []
    for ln in path.read_text().splitlines()[-limit:]:
        try:
            records.append(json.loads(ln))
        except Exception:
            pass
    return list(reversed(records))


# ── Bot control ───────────────────────────────────────────────────────────────

@app.post("/api/bot/start")
def api_start():
    bot = get_bot()
    if bot.is_running():
        return {"status": "already_running"}
    bot.start()
    get_bus().publish("state_update", bot.get_status())
    return {"status": "started"}


@app.post("/api/bot/stop")
def api_stop():
    bot = get_bot()
    bot.stop()
    get_bus().publish("state_update", bot.get_status())
    return {"status": "stopped"}


class BotConfig(BaseModel):
    demo_mode: Optional[bool] = None
    risk_pct:  Optional[float] = None


@app.post("/api/bot/configure")
def api_configure(cfg: BotConfig):
    bot = get_bot()
    bot.configure(demo_mode=cfg.demo_mode, risk_pct=cfg.risk_pct)
    return {"status": "updated", "config": bot.get_status()}


@app.post("/api/bot/flush")
def api_flush():
    """Trigger immediate Claude Opus analysis (min 5 trades required)."""
    bot = get_bot()
    bot.flush_daily_summary(notes="manual_trigger", run_analysis=True)
    return {"status": "triggered"}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bus = get_bus()
    q   = bus.subscribe()
    logger.info("WS client connected (%d total)", bus.subscriber_count())

    # Send full state immediately on connect
    try:
        init = json.dumps({
            "type": "init",
            "data": get_bot().get_status(),
        }, default=str)
        await websocket.send_text(init)
    except Exception:
        pass

    try:
        while True:
            msg = await asyncio.wait_for(q.get(), timeout=30.0)
            await websocket.send_text(msg)
    except asyncio.TimeoutError:
        # Send heartbeat to keep connection alive
        try:
            await websocket.send_text(json.dumps({"type": "heartbeat"}))
        except Exception:
            pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        bus.unsubscribe(q)
        logger.info("WS client disconnected (%d remaining)", bus.subscriber_count())


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "dashboard.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
