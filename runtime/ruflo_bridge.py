"""Ruflo MCP Bridge — Python client for the Ruflo MCP server.

Ruflo (https://github.com/ruvnet/ruflo) is a Node.js multi-agent
orchestration platform that exposes ~210 tools via the MCP protocol.
This bridge connects OpenClaw to Ruflo via MCP JSON-RPC over stdio.

Architecture:
    OpenClaw (Python) → RufloBridge (this file) → MCP JSON-RPC →
    Ruflo subprocess (Node.js) → swarm agents + HNSW memory

All Ruflo outputs are ADVISORY. They are fed into the IntentPipeline
as additional signals — never as execution-authoritative decisions.

Usage:
    bridge = RufloBridge()
    if bridge.start():
        result = bridge.call_tool("memory_search_unified",
                                  {"query": "BTC TRENDING_BULL RSI 72"})
        bridge.stop()

Run standalone test:
    python -m runtime.ruflo_bridge --test
"""
from __future__ import annotations

import json
import logging
import os
import queue as _queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("openclaw.runtime.ruflo_bridge")

# ── MCP protocol constants ────────────────────────────────────────────────────
_MCP_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "openclaw", "version": "1.0.0"}


@dataclass
class MCPToolResult:
    tool_name:  str
    success:    bool
    content:    Any                       # parsed response content
    raw:        Dict[str, Any] = field(default_factory=dict)
    error:      str = ""
    latency_ms: float = 0.0


class RufloBridge:
    """Manages a Ruflo MCP server subprocess and exposes its tools.

    Transport: MCP JSON-RPC over stdio (default) or HTTP+SSE.
    Falls back gracefully if Ruflo is not installed.
    """

    def __init__(
        self,
        transport:       str   = None,   # "stdio" | "http" — from env RUFLO_MCP_TRANSPORT
        http_port:       int   = None,   # from env RUFLO_MCP_HTTP_PORT
        timeout_s:       float = None,   # from env RUFLO_TIMEOUT_S
        memory_namespace: str  = None,   # from env RUFLO_MEMORY_NAMESPACE
    ):
        self._transport  = transport  or os.getenv("RUFLO_MCP_TRANSPORT", "stdio")
        self._http_port  = http_port  or int(os.getenv("RUFLO_MCP_HTTP_PORT", "3001"))
        self._timeout    = timeout_s  or float(os.getenv("RUFLO_TIMEOUT_S", "5"))
        self._namespace  = memory_namespace or os.getenv("RUFLO_MEMORY_NAMESPACE", "openclaw")

        self._proc:       Optional[subprocess.Popen] = None
        self._lock        = threading.Lock()
        self._req_id      = 0
        self._initialized = False
        self._available   = False
        self._tools:      List[str] = []

        # Per-request response queues — key: request_id, value: Queue(maxsize=1).
        # Eliminates response mismatch when concurrent callers share the same stdout.
        self._pending_rpcs:  Dict[int, _queue.Queue] = {}
        self._pending_lock   = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the Ruflo MCP server subprocess. Returns True if successful."""
        if self._transport == "http":
            return self._connect_http()
        return self._start_stdio()

    def stop(self) -> None:
        """Gracefully stop the Ruflo subprocess and reader thread."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._initialized = False
        self._available = False
        # Wake any callers blocked on their per-request queues so they time out
        # cleanly rather than waiting until their timeout expires.
        with self._pending_lock:
            for q in self._pending_rpcs.values():
                try:
                    q.put_nowait({"error": "bridge stopped"})
                except _queue.Full:
                    pass
        logger.info("RufloBridge: stopped")

    def is_available(self) -> bool:
        return self._available and self._initialized

    def available_tools(self) -> List[str]:
        return list(self._tools)

    # ── Tool calls ────────────────────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """Call a Ruflo MCP tool. Returns MCPToolResult (success or failure)."""
        if not self.is_available():
            return MCPToolResult(tool_name, False, None, error="Ruflo not available")

        t0 = time.monotonic()
        try:
            response = self._rpc("tools/call", {
                "name": tool_name,
                "arguments": arguments,
            })
            latency = (time.monotonic() - t0) * 1000
            if "error" in response:
                return MCPToolResult(
                    tool_name, False, None, raw=response,
                    error=str(response["error"]), latency_ms=latency,
                )
            content = response.get("result", {}).get("content", response.get("result"))
            return MCPToolResult(tool_name, True, content, raw=response, latency_ms=latency)
        except Exception as exc:
            return MCPToolResult(tool_name, False, None, error=str(exc),
                                 latency_ms=(time.monotonic() - t0) * 1000)

    # ── High-level trading helpers ────────────────────────────────────────────

    def memory_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search Ruflo's HNSW memory for similar past patterns."""
        result = self.call_tool("memory_search_unified", {
            "query": query,
            "namespace": self._namespace,
            "limit": limit,
        })
        if not result.success:
            return []
        # Normalize content to list
        content = result.content
        if isinstance(content, list):
            return content
        if isinstance(content, dict):
            return content.get("results", content.get("memories", [content]))
        return []

    def memory_store(self, key: str, content: str, metadata: Dict[str, Any] = None) -> bool:
        """Store a pattern in Ruflo's HNSW memory."""
        result = self.call_tool("memory_store", {
            "key":       key,
            "content":   content,
            "namespace": self._namespace,
            "metadata":  metadata or {},
        })
        return result.success

    def swarm_analyze(self, task: str, context: Dict[str, Any]) -> Optional[str]:
        """Ask the Ruflo swarm for a multi-agent analysis. Returns text summary."""
        swarm_size = int(os.getenv("RUFLO_SWARM_SIZE", "3"))
        result = self.call_tool("swarm_init", {
            "task":         task,
            "context":      context,
            "maxAgents":    swarm_size,
            "strategy":     "parallel",
        })
        if not result.success:
            return None
        # Extract text from content
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content:
            # MCP text content blocks
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(t for t in texts if t)
        if isinstance(content, dict):
            return content.get("summary") or content.get("result") or str(content)
        return str(content) if content else None

    def get_status(self) -> Dict[str, Any]:
        return {
            "available":   self._available,
            "initialized": self._initialized,
            "transport":   self._transport,
            "tool_count":  len(self._tools),
            "namespace":   self._namespace,
        }

    # ── MCP JSON-RPC (stdio) ──────────────────────────────────────────────────

    def _start_stdio(self) -> bool:
        """Start Ruflo as a stdio subprocess and perform MCP handshake."""
        cmd = self._find_ruflo_cmd()
        if cmd is None:
            logger.warning("Ruflo not installed — run: bash scripts/install_ruflo.sh")
            return False

        try:
            logger.info("Starting Ruflo MCP server: %s", " ".join(cmd))
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            # Give the process a moment to start
            time.sleep(1.5)
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read(500).decode(errors="replace")
                logger.error("Ruflo process exited immediately: %s", stderr)
                return False

            # Start reader thread before any RPC calls
            self._start_reader_thread()

            # MCP initialize handshake
            resp = self._rpc("initialize", {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities":    {},
                "clientInfo":      _CLIENT_INFO,
            })
            if "error" in resp:
                logger.error("Ruflo MCP initialize failed: %s", resp["error"])
                return False

            # Send initialized notification
            self._notify("notifications/initialized")

            # Discover available tools
            tools_resp = self._rpc("tools/list", {})
            tools = tools_resp.get("result", {}).get("tools", [])
            self._tools = [t.get("name", "") for t in tools if isinstance(t, dict)]

            self._initialized = True
            self._available = True
            logger.info(
                "Ruflo MCP connected — %d tools available  namespace=%s",
                len(self._tools), self._namespace,
            )
            return True

        except Exception as exc:
            logger.error("Ruflo bridge start failed: %s", exc)
            self.stop()
            return False

    def _connect_http(self) -> bool:
        """Connect to a running Ruflo HTTP+SSE server (not yet implemented)."""
        logger.warning("Ruflo HTTP transport not implemented — use stdio")
        return False

    # ── JSON-RPC primitives ───────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _start_reader_thread(self) -> None:
        """Start a background thread that reads Ruflo stdout and routes responses."""
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="ruflo-reader",
        )
        self._reader_thread.start()

    def _reader_loop(self) -> None:
        """Read JSON-RPC responses from Ruflo stdout; dispatch to per-request queues."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while True:
            try:
                raw = proc.stdout.readline()
            except Exception:
                break
            if not raw:
                break
            try:
                msg = json.loads(raw.decode().strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                continue  # notification — not a request response
            with self._pending_lock:
                q = self._pending_rpcs.get(msg_id)
            if q is not None:
                try:
                    q.put_nowait(msg)
                except _queue.Full:
                    pass  # caller already timed out and moved on

    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON-RPC request and return the response via a per-request queue.

        Concurrent callers each own their own queue — responses can never be
        consumed by the wrong caller.
        """
        req_id = self._next_id()
        response_q: _queue.Queue = _queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending_rpcs[req_id] = response_q
        try:
            request = {
                "jsonrpc": "2.0",
                "id":      req_id,
                "method":  method,
                "params":  params,
            }
            self._send(request)
            try:
                return response_q.get(timeout=self._timeout)
            except _queue.Empty:
                raise TimeoutError(f"Ruflo RPC timeout after {self._timeout}s (id={req_id})")
        finally:
            with self._pending_lock:
                self._pending_rpcs.pop(req_id, None)

    def _notify(self, method: str, params: Dict[str, Any] = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    def _send(self, obj: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Ruflo process not running")
        line = json.dumps(obj) + "\n"
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()


    # ── Command discovery ─────────────────────────────────────────────────────

    @staticmethod
    def _find_ruflo_cmd() -> Optional[List[str]]:
        """Find the ruflo MCP server command. Tries ruflo binary then npx."""
        import shutil
        # Try global install
        if shutil.which("ruflo"):
            return ["ruflo", "mcp", "start"]
        # Try npx (slower but works without global install)
        if shutil.which("npx"):
            return ["npx", "--yes", "ruflo@latest", "mcp", "start"]
        return None


# ── Singleton ─────────────────────────────────────────────────────────────────

_bridge: Optional[RufloBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> RufloBridge:
    """Return (or create) the shared RufloBridge singleton."""
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = RufloBridge()
        return _bridge


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Test Ruflo MCP bridge")
    parser.add_argument("--test", action="store_true", default=True)
    args = parser.parse_args()

    print("\n=== Ruflo Bridge Connection Test ===\n")
    bridge = RufloBridge()
    ok = bridge.start()
    if not ok:
        print("✗ Ruflo not available.")
        print("  Install: bash scripts/install_ruflo.sh")
        sys.exit(1)

    print(f"✓ Connected  tools={len(bridge.available_tools())}")
    print(f"  First 10 tools: {bridge.available_tools()[:10]}")

    # Test memory search
    results = bridge.memory_search("BTC TRENDING_BULL", limit=3)
    print(f"  Memory search 'BTC TRENDING_BULL': {len(results)} results")

    bridge.stop()
    print("\n✓ Bridge test complete\n")
