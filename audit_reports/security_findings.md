# Security Findings

## CRITICAL — Fixed

### SEC-1: Dashboard API Unauthenticated (FIXED)
**File:** `dashboard/api/server.py`  
**Impact:** Anyone on local network could start/stop bot, change risk_pct, read positions  
**Fix:** `_require_local_or_token` dependency — external requests require `X-Dashboard-Token` header  

### SEC-2: BotConfig Unbounded risk_pct (FIXED)
**Impact:** Unauthenticated caller could set `risk_pct: 99999`  
**Fix:** Pydantic `Field(ge=0.1, le=4.0)` bounds validation  

### SEC-3: Auth Default "changeme" Token (FIXED)
**File:** `security/auth.py`  
**Fix:** Warns loudly when default detected; logs at WARNING before every API start  

### SEC-4: Telegram Allowlist Fail-Open (FIXED)
**File:** `security/auth.py`  
**Fix:** Empty allowlist now denies all — requires explicit `TELEGRAM_ALLOWED_IDS` in `.env`  

## CRITICAL — NOT YET FIXED

### SEC-5: XOR "Encryption" in secrets.py
**File:** `security/secrets.py:48`  
**Severity:** CRITICAL for live deployment  
**Details:** `bytes(b ^ 0x42 for b in decoded)` — single-byte XOR is trivially reversible  
**Risk:** Any file system access (server compromise, backup leak) exposes all API keys  
**Required fix:** Replace with `cryptography.fernet.Fernet` with key stored in separate secure location  
**Blocking:** Live deployment  

## HIGH — Not Yet Fixed

### SEC-6: Emergency Controls Maker/Checker Bypass
**File:** `governance/emergency_controls.py:180`  
**Details:** Concurrent `emergency_halt_all()` calls can overwrite `_halt_operator`, allowing the wrong operator to "release" a halt they didn't create  
**Fix:** Store `halt_operator` in release request payload; compare against it, not live state  

### SEC-7: Approvals Log Concurrent-Write Corruption
**File:** `governance/approvals.py:213`  
**Details:** `_load()` and `_append_log()` can interleave without file-level locking  
**Fix:** Use `fcntl.flock()` or write-to-temp-then-rename pattern  

### SEC-8: Permissions Base64 "Encryption"
**File:** `governance/permissions.py:126`  
**Details:** Uses base64 which is encoding, not encryption. Advertised as "stub"  
**Fix:** Use `cryptography.fernet` or environment-only secrets before production  

## MEDIUM

### SEC-9: Telegram Command Injection Risk
**File:** `security/api_firewall.py:42`  
**Details:** Regex `^/(command)(\s.*)?$` captures arbitrary tail text, which if passed to shell/SQL downstream could enable injection  
**Fix:** Validate each argument against whitelist patterns independently  

### SEC-10: No Rate Limiting on API
**File:** `dashboard/api/server.py`  
**Details:** No per-IP rate limiting — state-change endpoints can be spammed  
**Fix:** Add `slowapi` middleware (1 req/sec for mutation endpoints)  

### SEC-11: WebSocket No Connection Limit
**File:** `dashboard/api/server.py`  
**Details:** Attacker can open unlimited WebSocket connections → memory exhaustion  
**Fix:** Cap at 20 concurrent WebSocket connections  
