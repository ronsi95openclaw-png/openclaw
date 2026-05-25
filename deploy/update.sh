#!/usr/bin/env bash
# Pull latest code and redeploy with zero-downtime rolling restart
# Run on the VPS: cd /opt/openclaw && bash deploy/update.sh
set -euo pipefail

BRANCH="claude/blofin-trading-bot-dashboard-TUJBC"

echo "[update] Pulling latest from $BRANCH..."
git pull origin "$BRANCH"

echo "[update] Rebuilding images..."
docker compose build --pull openclaw-bot openclaw-web

echo "[update] Rolling restart..."
docker compose up -d --no-deps openclaw-bot
sleep 10
docker compose up -d --no-deps openclaw-web

echo "[update] Status:"
docker compose ps

echo "[update] Done. API health:"
curl -s http://localhost:8000/api/health | python3 -m json.tool 2>/dev/null || echo "(API starting up)"
