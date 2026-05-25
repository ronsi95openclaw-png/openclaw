#!/usr/bin/env bash
# OpenClaw VPS Setup — run once as root on a fresh Ubuntu 22.04/24.04 server
# Usage: curl -fsSL https://raw.githubusercontent.com/ronsi95openclaw-png/openclaw/claude/blofin-trading-bot-dashboard-TUJBC/deploy/setup_vps.sh | bash
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
section() { echo -e "\n${GREEN}━━━ $* ━━━${NC}"; }

# ── 1. System packages ────────────────────────────────────────────────────────
section "System packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl git ufw fail2ban \
    nginx certbot python3-certbot-nginx \
    htop jq unzip

# ── 2. Docker ─────────────────────────────────────────────────────────────────
section "Docker"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    info "Docker installed"
else
    info "Docker already installed: $(docker --version)"
fi
systemctl enable --now docker

# ── 3. Firewall ───────────────────────────────────────────────────────────────
section "Firewall (ufw)"
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp    # HTTP (Let's Encrypt + redirect)
ufw allow 443/tcp   # HTTPS (dashboard)
ufw allow 8000/tcp  # Bot API (restrict to your IP after setup if preferred)
ufw allow 3000/tcp  # Dashboard UI (same)
ufw --force enable
info "Firewall active"

# ── 4. Fail2ban ───────────────────────────────────────────────────────────────
systemctl enable --now fail2ban

# ── 5. Clone repo ─────────────────────────────────────────────────────────────
section "OpenClaw repo"
INSTALL_DIR="/opt/openclaw"
BRANCH="claude/blofin-trading-bot-dashboard-TUJBC"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Repo exists — pulling latest"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
    git clone --branch "$BRANCH" \
        https://github.com/ronsi95openclaw-png/openclaw.git "$INSTALL_DIR"
    info "Repo cloned → $INSTALL_DIR"
fi
chown -R 1001:root "$INSTALL_DIR"

# ── 6. .env file ─────────────────────────────────────────────────────────────
section ".env setup"
ENV_FILE="$INSTALL_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<'ENVTEMPLATE'
# ── Crypto.com ────────────────────────────────────────────────────────────────
CRYPTOCOM_API_KEY=
CRYPTOCOM_SECRET=
DEMO_MODE=true

# ── Anthropic (Claude Opus nightly analysis) ──────────────────────────────────
ANTHROPIC_API_KEY=

# ── OpenRouter (cloud LLM fallback if Ollama slow) ───────────────────────────
OPENROUTER_API_KEY=
OPENROUTER_SITE_URL=https://openclaw.app
OPENROUTER_SITE_NAME=OpenClaw

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=8647354078:AAEbBwS6pqJ2_H6tXVWFXzj3mLcEO6s6ptk
TELEGRAM_CHAT_ID=6082698835

# ── Supabase (cloud state persistence) ────────────────────────────────────────
SUPABASE_URL=https://gotdcwcdcampwysydbzg.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdvdGRjd2NkY2FtcHd5c3lkYnpnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk3MzYxNzQsImV4cCI6MjA5NTMxMjE3NH0.ZLv8C6P83Ok08BuKpYkEvJs4LpP_6Sq7y3zc7errBG4

# ── Google Sheets (optional) ──────────────────────────────────────────────────
GOOGLE_SHEETS_CREDENTIALS_FILE=/app/credentials.json
GOOGLE_SHEET_ID=

# ── Dashboard API auth token ──────────────────────────────────────────────────
DASHBOARD_TOKEN=

# ── Ollama (set automatically by docker-compose) ──────────────────────────────
OLLAMA_HOST=http://ollama:11434
ENVTEMPLATE
    warn ".env created at $ENV_FILE — fill in your API keys before starting"
else
    info ".env already exists — skipping"
fi
chmod 600 "$ENV_FILE"

# ── 7. Pull Ollama models ─────────────────────────────────────────────────────
section "Pull Ollama models (this takes 5-15 min on first run)"
cd "$INSTALL_DIR"

# Start Ollama container first to pull models into the volume
docker compose up -d ollama
info "Waiting for Ollama to start..."
sleep 10

docker compose exec ollama ollama pull qwen2.5:14b  || warn "qwen2.5:14b pull failed — retry with: docker compose exec ollama ollama pull qwen2.5:14b"
docker compose exec ollama ollama pull qwen3        || warn "qwen3 pull failed"

# ── 8. Start everything ───────────────────────────────────────────────────────
section "Starting OpenClaw stack"
cd "$INSTALL_DIR"
docker compose build --pull
docker compose up -d

info "Waiting 30s for services to initialise..."
sleep 30
docker compose ps

# ── 9. Nginx reverse proxy ────────────────────────────────────────────────────
section "Nginx config"
PUBLIC_IP=$(curl -s ifconfig.me)
cat > /etc/nginx/sites-available/openclaw <<NGINX
server {
    listen 80;
    server_name $PUBLIC_IP _;

    # Dashboard UI
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Bot API + WebSocket
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Telegram webhook
    location /telegram/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/openclaw /etc/nginx/sites-enabled/openclaw
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 10. Systemd watchdog ──────────────────────────────────────────────────────
section "Systemd watchdog"
cat > /etc/systemd/system/openclaw.service <<SYSTEMD
[Unit]
Description=OpenClaw Trading Bot Stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable openclaw
info "openclaw.service enabled — starts on boot"

# ── 11. Summary ───────────────────────────────────────────────────────────────
section "Setup complete"
echo ""
echo "  Server IP   : $PUBLIC_IP"
echo "  Dashboard   : http://$PUBLIC_IP:3000"
echo "  API health  : http://$PUBLIC_IP:8000/api/health"
echo "  Bot API     : http://$PUBLIC_IP:8000/api/status"
echo ""
echo "  Next steps:"
echo "  1. Fill in API keys:  nano $ENV_FILE"
echo "  2. Add your server IP to Crypto.com API key whitelist"
echo "  3. Restart:           cd $INSTALL_DIR && docker compose restart"
echo "  4. View logs:         docker compose logs -f openclaw-bot"
echo ""
warn "DEMO_MODE=true in .env — set to false only after Ronnie explicitly approves"
