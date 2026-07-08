"""
JARVIS Gateway — WebSocket server at ws://127.0.0.1:18790 (configurable via JARVIS_GATEWAY_PORT)
Bridges JARVIS UI to OpenClaw brain.
"""
import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path

# websockets library — check if available
try:
    import websockets
    from websockets.asyncio.server import serve
except ImportError:
    raise RuntimeError("pip install websockets")

logger = logging.getLogger("openclaw.jarvis_gateway")

GATEWAY_TOKEN = os.getenv("GATEWAY_TOKEN", "").strip()
HOST = "127.0.0.1"
PORT = int(os.getenv("JARVIS_GATEWAY_PORT", "18790"))
SESSION_KEY = "agent:main:main"

# Connected + authenticated clients
_clients: dict = {}  # ws -> {"authed": bool}


async def _broadcast(msg: dict):
    """Send to all authenticated clients."""
    data = json.dumps(msg)
    dead = []
    for ws, state in _clients.items():
        if state.get("authed"):
            try:
                await ws.send(data)
            except Exception:
                dead.append(ws)
    for ws in dead:
        _clients.pop(ws, None)


async def _health_loop():
    while True:
        await asyncio.sleep(10)
        await _broadcast({"type": "event", "event": "health",
                          "payload": {"status": "ok", "ts": int(time.time() * 1000)}})


async def _handle_message(ws, raw: str):
    try:
        msg = json.loads(raw)
    except Exception:
        return

    msg_type = msg.get("type")
    msg_id   = msg.get("id", "0")
    method   = msg.get("method", "")
    params   = msg.get("params", {})

    async def reply(ok: bool, payload: dict = None, error: dict = None):
        r = {"type": "res", "id": msg_id, "ok": ok}
        if payload is not None: r["payload"] = payload
        if error is not None:   r["error"]   = error
        await ws.send(json.dumps(r))

    if method == "connect":
        token = (params.get("auth") or {}).get("token", "")
        if GATEWAY_TOKEN and token != GATEWAY_TOKEN:
            await reply(False, error={"message": "unauthorized"})
            await ws.close()
            return
        _clients[ws]["authed"] = True
        await reply(True, payload={"type": "hello-ok",
                                   "session": {"key": SESSION_KEY}, "auth": {}})
        return

    if not _clients.get(ws, {}).get("authed"):
        await reply(False, error={"message": "not authenticated"})
        return

    if method == "chat.send":
        content = params.get("content", "")
        session = params.get("sessionKey", SESSION_KEY)
        await reply(True, payload={})

        # Route to OpenClaw brain in executor
        loop = asyncio.get_running_loop()
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from core.brain import ask_hybrid
            from core.conversation import add_message, get_history
            add_message(0, "user", content)
            history = get_history(0)
            response, brain = await loop.run_in_executor(
                None, lambda: ask_hybrid(content, history=history)
            )
            add_message(0, "assistant", response)
        except Exception as e:
            response = f"Brain error: {e}"
            brain = "error"

        await _broadcast({"type": "event", "event": "chat", "payload": {
            "sessionKey": session,
            "state": "done",
            "message": {
                "role": "assistant",
                "content": response,
                "model": brain,
                "usage": {"inputTokens": 0, "outputTokens": 0}
            }
        }})
        return

    if method == "status.get":
        await reply(True, payload={
            "version": "0.8.0", "status": "running",
            "agents": [{"id": "main", "name": "ClawBot", "status": "active"}],
            "skills": []
        })
        return

    # Unknown — ack silently
    await reply(True, payload={})


async def _handler(ws):
    _clients[ws] = {"authed": False}
    # Send challenge
    nonce = secrets.token_hex(16)
    await ws.send(json.dumps({
        "type": "event", "event": "connect.challenge",
        "payload": {"nonce": nonce}
    }))
    try:
        async for raw in ws:
            await _handle_message(ws, raw)
    except Exception:
        pass
    finally:
        _clients.pop(ws, None)


async def run_gateway():
    logger.info(f"JARVIS Gateway starting on ws://{HOST}:{PORT}")
    asyncio.create_task(_health_loop())
    async with serve(_handler, HOST, PORT) as server:
        logger.info("JARVIS Gateway ready")
        await server.serve_forever()


def start_gateway_thread():
    """Start gateway in a background thread (called from receiver.py).
    Non-fatal: if port is already in use, logs a warning and skips."""
    import threading
    def _run():
        try:
            asyncio.run(run_gateway())
        except OSError as exc:
            logger.warning(f"JARVIS Gateway could not start (port in use?): {exc}")
        except Exception as exc:
            logger.error(f"JARVIS Gateway crashed: {exc}")
    t = threading.Thread(target=_run, daemon=True, name="jarvis-gateway")
    t.start()
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_gateway())
