# Audit Report — Infrastructure-as-Code (Phase 6)
**Date:** 2026-05-23
**Files:** `deployment/terraform/`, `deployment/k8s/`, `deployment/systemd/`
**Status:** IMPLEMENTED

## Summary
Three deployment targets providing reproducible, fail-closed, secret-isolated
infrastructure across VPS, Kubernetes, and systemd environments.

---

## Terraform (DigitalOcean-compatible VPS)

### main.tf
- Firewall: **fail-closed** — deny all inbound except ports 22/8000/3000/9090/3030
- Droplet: 2 vCPU / 4GB RAM, Docker installed via user_data
- Container secrets loaded from host environment file (NOT hardcoded)
- Null resource health probe: polls `/api/health` after provisioning
- Tags: openclaw, trading-bot, demo-mode

### variables.tf (10 variables)
- All sensitive variables marked `sensitive=true`: api_key, crypto_api_key, crypto_api_secret, telegram_bot_token
- `environment` validates to: demo | staging | production
- `docker_image` has no default — must be supplied at apply time
- `demo_mode` defaults to `true` — explicit override required for live

### outputs.tf
instance_ip, api_endpoint, dashboard_url, grafana_url, prometheus_url, health_check_url

---

## Kubernetes Manifests

### deployment.yaml
- Replicas: **1** (single-leader by design — `strategy: Recreate`)
- SecurityContext: `runAsNonRoot: true`, `runAsUser: 1000`
- All env vars from `secretKeyRef` or `configMapKeyRef` — nothing hardcoded
- Liveness: `GET /api/health`, `initialDelaySeconds: 30`, `periodSeconds: 10`
- Readiness: `GET /api/diagnostics`, `initialDelaySeconds: 10`, `periodSeconds: 5`
- PVC: openclaw-data → /app/data (persistent state survives pod restart)
- `terminationGracePeriodSeconds: 60` (bot gets 60s to drain)

### service.yaml
Two ClusterIP services: openclaw-api (8000), openclaw-dashboard (3000)
NodePort block commented out — explicit opt-in for development

### configmap.yaml
Non-sensitive defaults: DEMO_MODE=true, LOG_LEVEL=INFO, snapshot/integrity intervals

### prometheus.yaml
ServiceMonitor (15s scrape) + PrometheusRule with 7 alerting rules:
survivability degraded/critical, integrity CRITICAL, latency explosion,
WS instability, fill rate collapsed, execution degraded

---

## systemd Units

### openclaw.service
- `EnvironmentFile=/etc/openclaw/secrets.env` — secrets NEVER in unit file
- `NoNewPrivileges=true`, `ProtectSystem=strict`, `PrivateTmp=true`, `ProtectHome=true`
- `ReadWritePaths`: only data/ and log/ directories
- `SystemCallFilter=@system-service` — blocks dangerous syscalls
- `LimitAS=4G` — memory address space bounded
- `Restart=on-failure`, `RestartSec=10`, `StartLimitBurst=3`

### openclaw-dashboard.service
- `After=openclaw.service` + `Requires=openclaw.service`
- `ExecStartPre=npm run build` — ensures build is fresh on restart
- Inherits security hardening from openclaw.service

### openclaw-grafana.service
- Standard grafana-server with explicit `GF_PATHS_*` env vars
- `ProtectSystem=full`, `ReadWritePaths` scoped to grafana data/logs/run

## Security Properties
- No hardcoded secrets in any file
- All secret references via file, env, or K8s Secret references
- `.env` and `credentials.json` excluded at build time
- Firewall fail-closed on all three targets
- Rollback: Terraform supports `terraform apply` from previous state snapshot;
  K8s Deployment supports `kubectl rollout undo`; systemd supports `journalctl` + manual unit override
