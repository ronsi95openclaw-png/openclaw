#!/usr/bin/env bash
# Install Ruflo (claude-flow MCP server) for OpenClaw integration.
# Run from the repo root: bash scripts/install_ruflo.sh

set -euo pipefail

echo "=== OpenClaw: Ruflo MCP Server Installation ==="

# 1. Node.js >= 20 required
if ! command -v node &>/dev/null; then
    echo "ERROR: Node.js not found. Install Node.js >= 20 first."
    echo "  https://nodejs.org  or:  curl -fsSL https://fnm.vercel.app/install | bash"
    exit 1
fi

NODE_VER=$(node -e "process.exit(parseInt(process.version.slice(1)) < 20 ? 1 : 0)" 2>&1 || true)
NODE_MAJOR=$(node -e "console.log(parseInt(process.version.slice(1)))")
if [ "$NODE_MAJOR" -lt 20 ]; then
    echo "ERROR: Node.js >= 20 required (found $(node --version))"
    exit 1
fi
echo "✓ Node.js $(node --version)"

# 2. Install ruflo globally
echo ""
echo "Installing ruflo@latest via npm..."
npm install -g ruflo@latest 2>&1

# 3. Verify install
if ! command -v ruflo &>/dev/null && ! npx ruflo@latest --version &>/dev/null 2>&1; then
    echo "WARNING: ruflo command not found after install — will use npx fallback"
else
    echo "✓ ruflo installed: $(npx ruflo@latest --version 2>/dev/null || echo 'version unknown')"
fi

# 4. Create data dir for Ruflo memory
mkdir -p data/ruflo_memory
echo "✓ data/ruflo_memory created"

# 5. Write .env additions
if [ -f .env ]; then
    if ! grep -q "RUFLO_" .env; then
        cat >> .env <<'ENVEOF'

# ── Ruflo MCP Integration ────────────────────────────────────────────────────
RUFLO_ENABLED=true
RUFLO_MCP_TRANSPORT=stdio        # stdio | http
RUFLO_MCP_HTTP_PORT=3001         # only used when transport=http
RUFLO_MEMORY_NAMESPACE=openclaw  # HNSW memory namespace
RUFLO_SWARM_SIZE=3               # parallel analysis agents
RUFLO_TIMEOUT_S=30               # per-call timeout
ENVEOF
        echo "✓ .env updated with RUFLO_* variables"
    else
        echo "✓ .env already has RUFLO_ variables"
    fi
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Test the connection:"
echo "  python -m runtime.ruflo_bridge --test"
echo ""
echo "Run capability matrix:"
echo "  python -m runtime.capability_matrix"
