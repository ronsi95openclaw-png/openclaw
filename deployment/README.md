# Deployment Infrastructure

This directory contains deployment configurations at various scales.
**Read this before assuming something is running.**

---

## ACTIVE (currently deployed)

| System | Where | Config |
|--------|-------|--------|
| Bot + FastAPI | Railway | `railway.toml` + `Dockerfile` |
| State persistence | Supabase | `infra/supabase_client.py` |
| Telegram relay | Local machine | `runtime/telegram_relay.py` |
| Dashboard UI | Railway (same service) | `dashboard/web/` |

**Railway URL:** `https://cryptobot-production-18e1.up.railway.app`  
**Deploy command:** `git push origin claude/blofin-trading-bot-dashboard-TUJBC` (Railway auto-deploys)

---

## VPS-READY (configured, not currently deployed)

Run this to bring up the full bot stack on any VPS:

```bash
# From /home/user/openclaw:
docker-compose -f docker-compose.yml up -d

# Services started:
#   openclaw-bot   (Python bot + FastAPI, port 8000)
#   openclaw-web   (Next.js dashboard, port 3000)
#   prometheus     (metrics, port 9091 — localhost only)
#   ollama         (local LLM inference, port 11434)
```

---

## ASPIRATIONAL (configured but not deployed anywhere)

These exist as **design artifacts** for future scaling. Do not attempt to deploy unless explicitly planning a Kubernetes migration.

| System | Purpose | Config |
|--------|---------|--------|
| Kubernetes | Horizontal scaling | `deployment/k8s/` |
| Terraform | Multi-cloud IaC | `deployment/terraform/` |
| Prometheus (full) | Metrics collection | `infra/prometheus/` |
| Grafana | Metrics dashboard | `infra/grafana/` + `deployment/grafana/` |
| Loki | Log aggregation | `infra/loki/` |
| AlertManager | Alert routing | `deployment/alerts/` |
| Redis | Queue + locks | `infra/docker-compose.yml` (with Redis) |
| systemd services | VPS process management | `deployment/systemd/` |

Full infra stack (bot + Redis + Prometheus + Grafana + Loki + AlertManager):
```bash
docker-compose -f infra/docker-compose.yml up -d
```

---

## DEPLOYMENT DECISION TREE

```
Need to deploy a code change?
  → git push to feature branch → Railway auto-deploys

Need to run locally?
  → python main.py

Migrating to VPS?
  → Use docker-compose.yml (VPS-ready stack)

Scaling to Kubernetes?
  → Use deployment/k8s/ (aspirational — needs secrets management first)
```

---

*Last audited: 2026-05-28*
