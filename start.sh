#!/usr/bin/env bash
# OpenClaw — local launcher
# Usage: bash start.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         OPENCLAW LAUNCHER v1.0           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Check .env ────────────────────────────────
if [ ! -f ".env" ]; then
  echo "⚠   .env not found — copying .env.example (demo mode, no live trading)"
  cp .env.example .env
fi
set -a; source .env; set +a
echo "✓  .env loaded (DEMO_MODE=${DEMO_MODE:-true})"

# ── Python deps ───────────────────────────────
echo "→  Installing Python dependencies..."
pip install -r requirements.txt -q
echo "✓  Python deps OK"

# ── Node deps ─────────────────────────────────
echo "→  Installing Node dependencies..."
cd dashboard/web
npm install --silent
cd "$ROOT"
echo "✓  Node deps OK"

# ── Kill any stale processes ──────────────────
pkill -f "dashboard/api/server.py" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 1

# ── Start Python API backend ──────────────────
echo ""
echo "→  Starting API backend on http://localhost:8000 ..."
python dashboard/api/server.py &
API_PID=$!
echo "   PID: $API_PID"

# Wait for backend to be ready
for i in $(seq 1 15); do
  if curl -sf http://localhost:8000/api/status > /dev/null 2>&1; then
    echo "✓  API backend ready"
    break
  fi
  sleep 1
  if [ $i -eq 15 ]; then
    echo "⚠  API backend slow to start — check errors above"
  fi
done

# ── Start Next.js frontend ────────────────────
echo "→  Starting dashboard on http://localhost:3000 ..."
cd dashboard/web
npm run dev &
NEXT_PID=$!
cd "$ROOT"
echo "   PID: $NEXT_PID"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Dashboard  →  http://localhost:3000     ║"
echo "║  API docs   →  http://localhost:8000/docs║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Press Ctrl+C to stop both servers"
echo ""

# ── Wait / trap cleanup ───────────────────────
cleanup() {
  echo ""
  echo "Stopping servers..."
  kill $API_PID 2>/dev/null || true
  kill $NEXT_PID 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

wait
