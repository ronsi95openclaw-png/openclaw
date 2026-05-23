# Audit Report — CI/CD Release Pipeline (Phase 5)
**Date:** 2026-05-23  
**Files:** `.github/workflows/release.yml`, `.github/workflows/ci.yml`  
**Status:** IMPLEMENTED

## Summary
Automated release pipeline publishing versioned Docker images to GitHub Container Registry (ghcr.io) on every `v*.*.*` tag push. Extended `ci.yml` adds Phase 5 soak tests and module import verification.

## release.yml — 5-Job Pipeline

### Job 1: `validate`
- Checkout + Python 3.11 setup
- `pip install -r requirements.txt`
- `python -m py_compile` all critical modules
- `pytest tests/ -k "not test_100k_event_replay"` with 10-minute timeout
- Version extracted from git tag via `${{ github.ref_name }}`

### Job 2: `build-and-push`
- Multi-platform build: `linux/amd64,linux/arm64`
- Tags pushed: `latest`, `$VERSION`, `sha-$SHORT_SHA`, `canary-$SHORT_SHA`
- Registry: `ghcr.io/${{ github.repository }}`
- Secrets: `GITHUB_TOKEN` (auto-provided), `DOCKER_USERNAME`/`DOCKER_PASSWORD` for fallback

### Job 3: `verify-image`
- Pulls just-published image
- Runs `python -m runtime.capability_matrix` inside container
- Validates all Phase 5 module imports succeed

### Job 4: `publish-release`
- Creates GitHub Release with auto-generated changelog
- Attaches image digest as release asset
- Marks as latest release

### Job 5: `rollback-tag` (conditional)
- Runs only if any prior job fails
- Re-tags previous stable image as `latest`
- Posts failure notice to release notes

## ci.yml Extensions

### New: `soak-tests` job
- `continue-on-error: true` — soak failures are advisory, not blocking
- Runs all 14 Phase 5 soak tests
- Timeout: 120 s

### Extended: `docker-build` job
- Phase 5 module verification step added after image build
- Verifies all 10 Phase 5 modules import cleanly inside built image

### Unchanged: `unit-tests` job
- Excludes soak tests via `--ignore=tests/soak`
- Remains blocking (must pass for merge)

## Security Controls
- `.env` and `credentials.json` excluded via `.dockerignore`
- `setup.sh` excluded from image build context
- `GITHUB_TOKEN` scoped to `packages: write` only
- No secrets logged in build output
