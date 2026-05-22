# Deployment Hardening Report
**Files:** `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.github/workflows/ci.yml`
**Date:** 2026-05-22

## What Was Built

### Dockerfile
- Base: `python:3.11-slim`
- Non-root user: `openclaw` (UID 1001)
- Two exposed ports: 8000 (dashboard API), 9090 (Prometheus metrics)
- `HEALTHCHECK`: polls `http://localhost:8000/health` every 30s, 3 retries, 15s start delay
- Secrets excluded via `.dockerignore`: `.env`, `credentials.json`, `setup.sh`
- Data directory created at build time; overridden by volume mount in production

### .dockerignore
Excludes: `.env`, `credentials.json`, `setup.sh`, `__pycache__`, `node_modules`, `.next`, `data/logs/`, `data/optimization/`, `.git`

### docker-compose.yml
Three services:
| Service | Port | Notes |
|---------|------|-------|
| `openclaw` | 8000, 9090 | Bot + API, restart=unless-stopped |
| `dashboard` | 3000 | Next.js frontend, depends_on openclaw healthy |
| `prometheus` | 9091 | prom/prometheus:v2.52.0, 30-day retention |

Data volume: `./data:/app/data` (state, logs, optimization persist across restarts)

Credentials: `./credentials.json:/app/credentials.json:ro` (read-only mount)

### CI/CD Pipeline (`.github/workflows/ci.yml`)
Four jobs:

| Job | Trigger | What it does |
|-----|---------|-------------|
| `lint-and-type` | Every push | ruff E/W/F + `py_compile` all .py files |
| `unit-tests` | After lint | pytest with 40% coverage floor |
| `chaos-tests` | After unit | `tests/chaos/` — 39 scenarios |
| `security-scan` | Every push (parallel) | bandit -ll -ii (medium+ severity) |
| `docker-build` | After unit | Build image + verify all Phase 2 modules import |

## Security Controls in CI
- Bandit skips `B101` (assert) and `B311` (random) — both intentional in test/demo code
- Secrets never committed (`.dockerignore` + `.gitignore` enforced)
- Docker build runs without `.env` to verify no key leakage at build time

## Remaining Gaps
- No Docker image push to registry (requires secret config)
- No staging/canary deployment
- `dashboard/web/Dockerfile.web` not yet created (Next.js containerisation)
- Health endpoint at `/health` exists but returns minimal data; should include capital state + reconciliation status for container orchestrators

## Estimated Time to Full Production Docker
~4 hours: write `Dockerfile.web`, push image to GHCR, add `docker stack deploy` step.
