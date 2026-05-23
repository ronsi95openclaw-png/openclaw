# Security Hardening — Phase 4
**Files:** `governance/permissions.py`, `dashboard/api/server.py`
**Date:** 2026-05-23

## Problems Fixed

### R-8: permissions.py base64 "encryption" → Fernet
**Before:**
```python
encoded = base64.b64encode(raw).decode("ascii")
# Stored in permissions.b64 — not encrypted, trivially reversible
```

**After:**
- `cryptography.fernet.Fernet` symmetric encryption
- Key stored in `data/permissions.key` (chmod 600, auto-generated on first use)
- File renamed to `permissions.fernet`
- Legacy auto-migration: on first load, if `permissions.b64` exists and `permissions.fernet` doesn't, migrates and renames old file to `permissions.b64.migrated`
- Same key management pattern as `security/secrets.py`

**Key properties:**
- Fernet: AES-128-CBC + HMAC-SHA256, authenticated encryption
- Key auto-generated if missing: `Fernet.generate_key()`
- File permissions enforced: `os.chmod(key_path, 0o600)`

### R-3: Halt release endpoint rate limiting
**Before:** No rate limiting on `POST /admin/halt/release`

**After:** Token bucket rate limiter (`_IPRateLimiter`) applied per client IP:
- 5 tokens maximum
- Refill at 5/minute (1 per 12 seconds)
- Fail-closed: any internal state corruption → request denied
- Returns HTTP 429 with descriptive message

```python
# Token bucket formula:
elapsed = now - bucket["last"]
refill = elapsed / period * max_tokens
bucket["tokens"] = min(max_tokens, bucket["tokens"] + refill)
if bucket["tokens"] >= 1:
    bucket["tokens"] -= 1
    return True   # allow
return False  # deny → HTTP 429
```

**Applies to:** `POST /admin/halt/release` only (high-value endpoint).
Other admin endpoints can be extended via `_halt_rate_limiter.is_allowed(ip)` reuse.

### CC-11: is_active() Concurrency Fix
**Before:**
```python
def is_active(self) -> bool:
    return self._active  # reads without lock → data race
```

**After:**
```python
def is_active(self) -> bool:
    with self._lock:
        return self._active  # always thread-safe

# Also: process_signal() now calls self.is_active() instead of self._active
```

## Security Posture After Phase 4

| Control | Before | After |
|---------|--------|-------|
| Permission store encryption | base64 (none) | Fernet AES-128-CBC + HMAC |
| Halt release brute-force protection | None | Token bucket: 5 attempts/min per IP |
| Halt release HMAC code | ✅ (Phase 3) | ✅ (retained) |
| Maker/checker enforcement | ✅ (Phase 3) | ✅ (retained) |
| CORS configurable | ✅ (Phase 3) | ✅ (retained) |
| Exchange keys | Never logged/committed | ✅ |
| Orchestrator race condition (CC-11) | Unfixed | ✅ Fixed |
