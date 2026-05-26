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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dashboard.api.event_bus import get_bus
from security.auth import TokenAuth

logger    = logging.getLogger("openclaw.dashboard.server")
_auth     = TokenAuth()   # reads DASHBOARD_TOKEN from env on startup


# ── Per-IP rate limiter (token bucket) ───────────────────────────────────────

class _IPRateLimiter:
    """Token-bucket rate limiter keyed by IP address.

    Default: 5 tokens, refill at 5/minute.  Fail-closed: if internal state
    is corrupted, the request is denied.
    """

    def __init__(self, max_tokens: int = 5, refill_period_s: float = 60.0) -> None:
        self._max = max_tokens
        self._period = refill_period_s
        self._buckets: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        try:
            now = time.monotonic()
            with self._lock:
                bucket = self._buckets.setdefault(ip, {"tokens": self._max, "last": now})
                elapsed = now - bucket["last"]
                # Refill tokens proportionally to elapsed time
                refill = elapsed / self._period * self._max
                bucket["tokens"] = min(self._max, bucket["tokens"] + refill)
                bucket["last"] = now
                if bucket["tokens"] >= 1:
                    bucket["tokens"] -= 1
                    return True
                return False
        except Exception:
            return False  # fail-closed


_halt_rate_limiter = _IPRateLimiter(max_tokens=5, refill_period_s=60.0)


def _require_local_or_token(request: Request) -> None:
    """Allow unauthenticated access from localhost; require token from external origins."""
    if _auth.is_local_request(request.client.host if request.client else ""):
        return
    token = request.headers.get("X-Dashboard-Token", "")
    if not _auth.verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Dashboard-Token")

app = FastAPI(title="OpenClaw Dashboard", version="1.0.0")

_DEFAULT_CORS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env else _DEFAULT_CORS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Dashboard-Token"],
)

# ── Bot singleton ─────────────────────────────────────────────────────────────

_bot = None


def get_bot():
    """Return the running CryptoComBot.

    Prefers the bot already started by main.py (found via the
    TelegramCommandBot singleton) to avoid creating a duplicate instance that
    would write conflicting events to the shared event store.
    Falls back to creating a new instance only when running API-only (no main.py).
    """
    global _bot
    if _bot is None:
        # First choice: get the bot_ref already wired into the cmd singleton
        try:
            from runtime.telegram_bot import _cmd_bot  # module-level singleton
            if _cmd_bot is not None and getattr(_cmd_bot, "_bot_ref", None) is not None:
                _bot = _cmd_bot._bot_ref
                return _bot
        except Exception:
            pass
        # Fallback: API-only mode (no main.py running)
        from trading.cryptocom_bot import CryptoComBot
        _bot = CryptoComBot()
    return _bot


# ── Startup / Shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    bus = get_bus()
    bus.set_loop(asyncio.get_event_loop())
    asyncio.create_task(_poll_bot_state())

    # Phase 9: telemetry polling loop
    from dashboard.api.telemetry import run_telemetry_loop
    asyncio.create_task(run_telemetry_loop())

    # Phase 9: include v2 router (lazy import so server still starts if router has issues)
    try:
        from dashboard.api.routers.phase9 import router as phase9_router
        app.include_router(phase9_router)
    except Exception as _phase9_exc:
        logger.warning("Phase 9 router failed to load: %s", _phase9_exc)

    # Register Telegram webhook if RAILWAY_PUBLIC_URL is set
    _register_telegram_webhook()

    logger.info("Dashboard API started — WebSocket event bus active")


def _register_telegram_webhook() -> None:
    """Set Telegram webhook to this Railway deployment's public URL.

    Only runs when RAILWAY_PUBLIC_URL is set (cloud/webhook mode).
    In local mode (no RAILWAY_PUBLIC_URL), deletes any stale webhook so that
    getUpdates long-polling works without 409 Conflict errors.
    """
    import urllib.request as _ureq
    railway_url = os.getenv("RAILWAY_PUBLIC_URL", "").rstrip("/")
    tok         = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not tok:
        logger.warning("Telegram: TELEGRAM_BOT_TOKEN not set — commands will not work")
        return

    if not railway_url:
        # Local/dev mode — delete any active webhook so long-polling works
        try:
            req = _ureq.Request(
                f"https://api.telegram.org/bot{tok}/deleteWebhook",
                data=json.dumps({"drop_pending_updates": False}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with _ureq.urlopen(req, timeout=8) as r:
                result = json.loads(r.read().decode())
            if result.get("ok"):
                logger.info("Telegram: stale webhook cleared — long-poll mode active")
            else:
                logger.debug("Telegram: deleteWebhook response: %s", result)
        except Exception as exc:
            logger.debug("Telegram: deleteWebhook skipped (%s)", exc)
        return

    # Cloud/Railway mode — register webhook
    webhook_url = f"{railway_url}/telegram/webhook"
    secret      = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    payload     = {"url": webhook_url, "allowed_updates": ["message"]}
    if secret:
        payload["secret_token"] = secret
    req = _ureq.Request(
        f"https://api.telegram.org/bot{tok}/setWebhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with _ureq.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode())
        if result.get("ok"):
            logger.info("Telegram webhook registered → %s", webhook_url)
        else:
            logger.warning("Telegram webhook registration failed: %s — "
                           "run setWebhook manually from a non-cloud machine", result)
    except Exception as exc:
        logger.warning("Telegram webhook registration error (%s) — "
                       "register manually: POST api.telegram.org/bot.../setWebhook", exc)


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


@app.get("/api/goal")
def api_goal() -> Dict[str, Any]:
    """Return $98 → $50,000 goal progress with milestones and ETA."""
    bot = get_bot()
    try:
        from runtime.goal_tracker import get_goal_tracker
        tracker = getattr(bot, "_goal_tracker", None) or get_goal_tracker(
            starting_balance=getattr(bot.state, "starting_balance", 98.0),
        )
        balance = bot._refresh_balance()
        progress = tracker.update(balance)
        from dataclasses import asdict
        return asdict(progress)
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/skill-clock")
def api_skill_clock() -> Dict[str, Any]:
    """Return Skill Clock status (last tick, regimes, action)."""
    bot = get_bot()
    clock = getattr(bot, "_skill_clock", None)
    if clock is None:
        return {"status": "unavailable"}
    return clock.get_status()


@app.get("/api/quin")
def api_quin() -> Dict[str, Any]:
    """Return QUIN orchestrator status (last decision, model, source)."""
    bot = get_bot()
    quin = getattr(bot, "_quin", None)
    if quin is None:
        return {"status": "unavailable"}
    return quin.get_status()


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


@app.get("/api/balance-history")
def api_balance_history(limit: int = 200):
    """Balance snapshots for the dashboard chart — last N scan ticks."""
    history = getattr(get_bot().state, "balance_history", [])
    return history[-min(limit, 500):]


@app.get("/health")
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
def api_start(_: None = Depends(_require_local_or_token)):
    bot = get_bot()
    if bot.is_running():
        return {"status": "already_running"}
    bot.start()
    get_bus().publish("state_update", bot.get_status())
    return {"status": "started"}


@app.post("/api/bot/stop")
def api_stop(_: None = Depends(_require_local_or_token)):
    bot = get_bot()
    bot.stop()
    get_bus().publish("state_update", bot.get_status())
    return {"status": "stopped"}


class BotConfig(BaseModel):
    demo_mode: Optional[bool]  = None
    risk_pct:  Optional[float] = Field(default=None, ge=0.1, le=4.0)


@app.post("/api/bot/configure")
def api_configure(cfg: BotConfig, _: None = Depends(_require_local_or_token)):
    bot = get_bot()
    bot.configure(demo_mode=cfg.demo_mode, risk_pct=cfg.risk_pct)
    return {"status": "updated", "config": bot.get_status()}


@app.post("/api/bot/flush")
def api_flush():
    """Trigger immediate Claude Opus analysis (min 5 trades required)."""
    bot = get_bot()
    bot.flush_daily_summary(notes="manual_trigger", run_analysis=True)
    return {"status": "triggered"}


# ── Telegram webhook (cloud mode) ─────────────────────────────────────────────

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook (used in Railway cloud deployment)."""
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if secret:
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header_token != secret:
            return Response(status_code=401)

    try:
        update = await request.json()
    except Exception:
        return {"ok": False}

    # Audit store (fire-and-forget)
    try:
        from infra.state_store import store_telegram_update
        store_telegram_update(update)
    except Exception:
        pass

    # Dispatch through the TelegramCommandBot singleton — same bot_ref as
    # long-poll mode so /status reads the actual running bot, not a stale copy.
    # Run in a background thread so we return {"ok": True} immediately
    # (Telegram retries after 5s if the handler blocks the response).
    try:
        import asyncio
        from runtime.telegram_bot import get_command_bot
        cmd_bot = get_command_bot()
        if cmd_bot:
            asyncio.create_task(asyncio.to_thread(cmd_bot._dispatch, update))
        else:
            from runtime.telegram_bot import _COMMANDS, _reply
            msg     = update.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text    = (msg.get("text") or "").strip()
            if chat_id and text.startswith("/"):
                cmd     = text.split()[0].split("@")[0].lower()
                handler = _COMMANDS.get(cmd)
                if handler:
                    asyncio.create_task(asyncio.to_thread(handler, chat_id, text, None))
    except Exception as exc:
        logger.warning("Telegram webhook dispatch error: %s", exc)

    return {"ok": True}


# ── Emergency halt release ─────────────────────────────────────────────────────

class HaltReleaseRequest(BaseModel):
    operator_id: str = Field(..., min_length=1, max_length=64)
    reason:      str = Field(..., min_length=10, max_length=500)
    release_code: str = Field(..., min_length=4, max_length=64)


@app.post("/admin/halt/release")
def admin_halt_release(
    req: HaltReleaseRequest,
    request: Request,
    _: None = Depends(_require_local_or_token),
) -> Dict[str, Any]:
    """Safe emergency halt release.

    Pre-conditions (all must pass before release is attempted):
      1. A reconciliation must have passed within the last 10 minutes.
      2. No unresolved CRITICAL reconciliation mismatches.
      3. Capital state must not be corrupted.
      4. Operator must be ADMIN and must not be the halt-setter (maker/checker).
      5. release_code must match HALT_RELEASE_CODE env var.

    Returns 200 with trace_id on success, 4xx on failure.
    """
    # Rate limit: max 5 attempts per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _halt_rate_limiter.is_allowed(client_ip):
        raise HTTPException(429, detail="Rate limit exceeded — max 5 halt release attempts per minute")

    import uuid, hashlib
    from datetime import timezone

    trace_id = str(uuid.uuid4())

    # ── Guard 1: release_code verification ────────────────────────────────────
    expected_code = os.getenv("HALT_RELEASE_CODE", "").strip()
    if not expected_code:
        raise HTTPException(503, detail="HALT_RELEASE_CODE not configured — contact admin")
    if not hmac_compare(req.release_code, expected_code):
        logger.warning("halt/release: invalid release_code from operator=%s", req.operator_id)
        raise HTTPException(403, detail="Invalid release code")

    # ── Guard 2: reconciliation freshness ─────────────────────────────────────
    bot = get_bot()
    recon_ok = False
    recon_age_s = None
    if hasattr(bot, "_recon_scheduler") and bot._recon_scheduler:
        report = bot._recon_scheduler.get_last_report()
        if report and report.passed:
            age = time.time() - (
                datetime.fromisoformat(report.ts).timestamp()
                if isinstance(report.ts, str) else report.ts
            )
            recon_age_s = round(age, 1)
            if age < 600:   # 10 minutes
                recon_ok = True
    if not recon_ok:
        raise HTTPException(409, detail=(
            f"Reconciliation not recently passed (age={recon_age_s}s). "
            "Run reconciliation first."
        ))

    # ── Guard 3: no active CRITICAL mismatches ────────────────────────────────
    if hasattr(bot, "_recon_scheduler") and bot._recon_scheduler:
        if bot._recon_scheduler.should_halt_entries():
            raise HTTPException(409, detail="Unresolved CRITICAL reconciliation mismatch — cannot release halt")

    # ── Guard 4: invoke emergency controls ────────────────────────────────────
    try:
        from governance.emergency_controls import EmergencyControls
        from governance.permissions import PermissionRegistry, OperatorPermission

        controls = EmergencyControls()
        if not controls.is_emergency_halted():
            return {"status": "no_halt_active", "trace_id": trace_id,
                    "message": "No emergency halt is currently active"}

        request_id = controls.request_halt_release(
            operator_id=req.operator_id,
            reason=req.reason,
        )
        released = controls.execute_approved_release(
            request_id=request_id,
            executing_operator=req.operator_id,
        )
        if not released:
            raise HTTPException(409, detail="Release rejected — maker/checker or approval failure")

        logger.critical(
            "[HALT RELEASED] operator=%s reason=%s trace_id=%s",
            req.operator_id, req.reason[:80], trace_id,
        )
        _send_halt_release_alert(req.operator_id, req.reason, trace_id)

        return {
            "status":     "released",
            "trace_id":   trace_id,
            "request_id": request_id,
            "operator":   req.operator_id,
            "reason":     req.reason,
            "ts":         datetime.now(timezone.utc).isoformat(),
        }
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(403, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("halt/release error: %s", exc, exc_info=True)
        raise HTTPException(500, detail=f"Internal error: {exc}")


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    import hmac as _hmac
    return _hmac.compare_digest(a.encode(), b.encode())


def _send_halt_release_alert(operator_id: str, reason: str, trace_id: str) -> None:
    try:
        from runtime.telegram_alerts import send_alert
        send_alert(
            f"[EMERGENCY HALT RELEASED]\n"
            f"Operator: {operator_id}\n"
            f"Reason: {reason[:120]}\n"
            f"Trace: {trace_id}"
        )
    except Exception:
        pass


def _get_survivability_score() -> Optional[float]:
    """Return survivability score (0–100) or None if engine unavailable."""
    try:
        from runtime.survivability import get_survivability_engine
        return get_survivability_engine().compute_score().current_score
    except Exception:
        return None


# ── Diagnostics ───────────────────────────────────────────────────────────────

@app.get("/api/diagnostics")
def api_diagnostics(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Full subsystem health snapshot."""
    try:
        from runtime.diagnostics import get_diagnostics_engine
        engine = get_diagnostics_engine()
        report = engine.run_full_check()
        return {
            "generated_at":             report.generated_at,
            "overall_status":           report.overall_status.value,
            "capital_state":            report.capital_state,
            "open_positions":           report.open_positions,
            "reconciliation_status":    report.reconciliation_status,
            "last_reconciliation_ts":   report.last_reconciliation_ts,
            "drift_events_active":      report.drift_events_active,
            "websocket_connections":    report.websocket_connections,
            "replay_journal_events":    report.replay_journal_event_count,
            "event_store_last_seq":     report.event_store_last_seq,
            "memory_mb":                report.memory_mb,
            "thread_count":             report.thread_count,
            "open_fds":                 report.open_fds,
            "uptime_seconds":           report.uptime_seconds,
            "recent_critical_incidents": report.recent_critical_incidents,
            "survivability_score": _get_survivability_score(),
            "subsystems": {
                name: {
                    "status":  h.status.value,
                    "latency_ms": h.latency_ms,
                    "error":   h.error,
                    "details": h.details,
                }
                for name, h in report.subsystems.items()
            },
        }
    except ImportError:
        # Diagnostics module not yet built — return basic health
        bot = get_bot()
        return {
            "overall_status": "UNKNOWN",
            "capital_state": "UNKNOWN",
            "open_positions": len(bot.state.open_positions),
            "message": "Full diagnostics module not available",
        }


# ── Phase 5 endpoints ────────────────────────────────────────────────────────

@app.get("/api/survivability")
def api_survivability(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Operational survivability score (0–100) across all subsystems."""
    try:
        from runtime.survivability import get_survivability_engine
        report = get_survivability_engine().compute_score()
        return {
            "current_score":        report.current_score,
            "classification":       report.classification.value,
            "deployment_ready":     report.deployment_ready,
            "degradation_trend":    report.degradation_trend,
            "critical_subsystems":  report.critical_subsystems,
            "generated_at":         report.generated_at,
            "subsystems": {
                name: {
                    "score":         ss.score,
                    "weight":        ss.weight,
                    "status_detail": ss.status_detail,
                    "last_updated":  ss.last_updated,
                }
                for name, ss in report.subsystem_scores.items()
            },
        }
    except ImportError:
        return {"current_score": None, "classification": "UNKNOWN",
                "message": "Survivability engine not yet available"}
    except Exception as exc:
        raise HTTPException(500, detail=f"Survivability check failed: {exc}")


@app.get("/api/integrity")
def api_integrity(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Run on-demand integrity scan across EventStore, snapshots, and replay."""
    try:
        from runtime.integrity_monitor import get_monitor
        report = get_monitor().run_scan()
        return {
            "generated_at":     report.generated_at,
            "overall_severity": report.overall_severity.value,
            "events_scanned":   report.events_scanned,
            "snapshots_checked":report.snapshots_checked,
            "scan_duration_ms": report.scan_duration_ms,
            "findings": [
                {
                    "finding_id":       f.finding_id,
                    "severity":         f.severity.value,
                    "subsystem":        f.subsystem,
                    "description":      f.description,
                    "detected_at":      f.detected_at,
                    "remediation_hint": f.remediation_hint,
                    "auto_halt":        f.auto_halt,
                }
                for f in report.findings
            ],
        }
    except ImportError:
        return {"overall_severity": "UNKNOWN",
                "message": "Integrity monitor not yet available"}
    except Exception as exc:
        raise HTTPException(500, detail=f"Integrity scan failed: {exc}")


@app.get("/api/snapshot-status")
def api_snapshot_status(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Snapshot daemon health and last snapshot metadata."""
    try:
        from runtime.snapshot_daemon import get_daemon
        return get_daemon().get_status()
    except ImportError:
        return {"running": False, "message": "Snapshot daemon not yet available"}
    except Exception as exc:
        raise HTTPException(500, detail=f"Snapshot status failed: {exc}")


@app.get("/api/execution-analytics")
def api_execution_analytics(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Execution analytics report (slippage, fill efficiency, latency)."""
    try:
        from runtime.execution_analytics import ExecutionAnalyticsEngine
        eng = ExecutionAnalyticsEngine()
        try:
            eng.load_from_file("data/logs/trade_outcomes.jsonl")
        except Exception:
            pass
        report = eng.generate_report()
        return {k: v for k, v in report.__dict__.items()
                if not k.startswith("_")} if hasattr(report, "__dict__") else {}
    except ImportError:
        return {"message": "Execution analytics not yet available"}
    except Exception as exc:
        raise HTTPException(500, detail=f"Analytics failed: {exc}")


@app.get("/api/alpha-validation")
def api_alpha_validation(_: None = Depends(_require_local_or_token)) -> Dict[str, Any]:
    """Statistical alpha validation report across all strategies."""
    try:
        from research.statistics.alpha_validation import AlphaValidationEngine
        engine = AlphaValidationEngine()
        engine.load_outcomes()
        report = engine.generate_report()
        return {
            "generated_at":                   report.generated_at,
            "portfolio_alpha_signal":         report.portfolio_alpha_signal.value,
            "overall_portfolio_expectancy":   report.overall_portfolio_expectancy,
            "trades_analyzed":                report.trades_analyzed,
            "alpha_collapsed_strategies":     report.alpha_collapsed_strategies,
            "degrading_strategies":           report.degrading_strategies,
            "insufficient_sample_strategies": report.insufficient_sample_strategies,
            "strategies": {
                name: {
                    "alpha_signal":              m.alpha_signal.value,
                    "rolling_sharpe":            m.rolling_sharpe,
                    "rolling_win_rate":          m.rolling_win_rate,
                    "rolling_expectancy_usd":    m.rolling_expectancy_usd,
                    "sample_size":               m.sample_size,
                    "statistical_significance":  m.statistical_significance,
                    "win_rate_decay_rate":        m.win_rate_decay_rate,
                }
                for name, m in report.strategies.items()
            },
        }
    except ImportError:
        return {"portfolio_alpha_signal": "UNKNOWN",
                "message": "Alpha validation not yet available"}
    except Exception as exc:
        raise HTTPException(500, detail=f"Alpha validation failed: {exc}")


# ── WebSocket ─────────────────────────────────────────────────────────────────

_WS_MAX_CONNECTIONS = 20
_ws_connection_count = 0
_ws_count_lock = asyncio.Lock()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _ws_connection_count
    async with _ws_count_lock:
        if _ws_connection_count >= _WS_MAX_CONNECTIONS:
            await websocket.close(code=1008, reason="Too many connections")
            logger.warning("WS: rejected connection — limit of %d reached", _WS_MAX_CONNECTIONS)
            return
        _ws_connection_count += 1

    await websocket.accept()
    bus = get_bus()
    q   = bus.subscribe()
    logger.info("WS client connected (%d/%d)", _ws_connection_count, _WS_MAX_CONNECTIONS)

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
        async with _ws_count_lock:
            _ws_connection_count = max(0, _ws_connection_count - 1)
        logger.info("WS client disconnected (%d/%d remaining)",
                    _ws_connection_count, _WS_MAX_CONNECTIONS)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        app,                 # pass object directly — works regardless of cwd
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
