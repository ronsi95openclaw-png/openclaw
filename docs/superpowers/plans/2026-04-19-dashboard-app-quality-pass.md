# Dashboard App-Quality Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenClaw dashboard fully functional as a daily-use app — working chat, graceful errors, inline feedback, config health banner, and mobile-ready new components.

**Architecture:** Two files change: `core/brain.py` gets a model auto-detect layer with lazy TTL cache; `dashboard/app.py` gets a toast system, inline command status bar, config health banner, improved error cards, and a global 500 handler — all self-contained in the existing HTML string templates and JS blocks.

**Tech Stack:** Python 3, Flask, Ollama Python SDK, Anthropic SDK, vanilla JS (no new libraries), pytest for unit tests.

---

## File Map

| File | What changes |
|------|-------------|
| `core/brain.py` | `ask_llm()` — add model auto-detect with 60s lazy TTL cache; add `OllamaOfflineError`; fix `ask_hybrid()` to catch it and fall back to Claude |
| `dashboard/app.py` | `DASHBOARD_HTML` — add `#cmd-status` bar, `#toast-container`, `#config-banner`, new JS functions; `api_system_status` extension for model mismatch; holdings error card improvement; global `@app.errorhandler(500)`; mobile CSS |
| `tests/test_brain_model_resolve.py` | New — unit tests for model resolution logic |

---

## Task 1: Ollama Model Auto-Detect in core/brain.py

**Files:**
- Modify: `core/brain.py` (lines 36, 225–246)
- Create: `tests/test_brain_model_resolve.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_brain_model_resolve.py`:

```python
"""Tests for Ollama model auto-detection in core/brain.py."""
from __future__ import annotations
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_brain():
    """Reload brain module with clean state."""
    sys.modules.pop("core.brain", None)
    import core.brain as b
    # Reset the module-level cache
    b._resolved_model = None
    b._resolved_model_ts = 0.0
    return b


def test_uses_configured_model_when_installed():
    """If OLLAMA_MODEL env is set and that model is installed, use it."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "qwen2.5:14b"

    with patch.dict("os.environ", {"OLLAMA_MODEL": "qwen2.5:14b"}):
        with patch("ollama.list") as mock_list:
            mock_list.return_value = MagicMock(models=[mock_model])
            resolved = brain._resolve_ollama_model()

    assert resolved == "qwen2.5:14b"


def test_falls_back_to_first_available_when_configured_missing():
    """If OLLAMA_MODEL is not installed, use the first installed model."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    with patch.dict("os.environ", {"OLLAMA_MODEL": "qwen2.5:14b"}):
        with patch("ollama.list") as mock_list:
            mock_list.return_value = MagicMock(models=[mock_model])
            resolved = brain._resolve_ollama_model()

    assert resolved == "mistral:latest"


def test_raises_offline_error_when_ollama_unreachable():
    """If ollama.list() raises, _resolve_ollama_model raises OllamaOfflineError."""
    brain = _fresh_brain()

    with patch("ollama.list", side_effect=Exception("connection refused")):
        try:
            brain._resolve_ollama_model()
            assert False, "Expected OllamaOfflineError"
        except brain.OllamaOfflineError:
            pass


def test_cache_avoids_repeated_ollama_list_calls():
    """Model is resolved once; second call within TTL skips ollama.list()."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    with patch("ollama.list") as mock_list:
        mock_list.return_value = MagicMock(models=[mock_model])
        brain._resolve_ollama_model()
        brain._resolve_ollama_model()

    assert mock_list.call_count == 1  # cached on second call


def test_cache_expires_after_ttl():
    """Cache expires after MODEL_CACHE_TTL seconds."""
    brain = _fresh_brain()
    brain.MODEL_CACHE_TTL = 0  # instant expiry
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    with patch("ollama.list") as mock_list:
        mock_list.return_value = MagicMock(models=[mock_model])
        brain._resolve_ollama_model()
        brain._resolve_ollama_model()

    assert mock_list.call_count == 2  # cache expired, re-fetched
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:/Users/ronsi95openclaw/Claude-openclaw
.venv/Scripts/python -m pytest tests/test_brain_model_resolve.py -v
```

Expected: 5 failures — `OllamaOfflineError`, `_resolve_ollama_model`, `MODEL_CACHE_TTL` not found.

- [ ] **Step 3: Add `OllamaOfflineError`, `_resolve_ollama_model`, cache vars, and update `ask_llm()` in `core/brain.py`**

After the existing constants block (after line 52, before `CLAWBOT_SYSTEM`), add:

```python
# ---------------------------------------------------------------------------
# Ollama model resolution — auto-detect with lazy TTL cache
# ---------------------------------------------------------------------------

class OllamaOfflineError(RuntimeError):
    """Raised when Ollama is unreachable (connection refused, not running)."""

MODEL_CACHE_TTL = 60  # seconds before re-checking installed models

_resolved_model: Optional[str] = None
_resolved_model_ts: float = 0.0


def _resolve_ollama_model() -> str:
    """Return the best available Ollama model, with 60-second TTL cache.

    Resolution order:
      1. OLLAMA_MODEL env var, if that model is installed.
      2. First model returned by ollama.list().
      3. OllamaOfflineError if ollama.list() raises (Ollama not running).
    """
    global _resolved_model, _resolved_model_ts
    now = time.time()
    if _resolved_model is not None and (now - _resolved_model_ts) < MODEL_CACHE_TTL:
        return _resolved_model

    try:
        from ollama import list as _ol_list
        models = [m.model for m in _ol_list().models]
    except Exception as exc:
        raise OllamaOfflineError(f"Ollama unreachable: {exc}") from exc

    if not models:
        raise OllamaOfflineError("Ollama is running but has no models installed.")

    configured = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    chosen = configured if configured in models else models[0]

    _resolved_model = chosen
    _resolved_model_ts = now
    return chosen
```

Then update `ask_llm()` — replace the existing body (lines 225–246) with:

```python
def ask_llm(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """Ask local Ollama. Uses auto-detected model unless overridden."""
    resolved = model or _resolve_ollama_model()  # may raise OllamaOfflineError
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(_compress_history(history))
    messages.append({"role": "user", "content": prompt})

    try:
        response = ollama_chat(model=resolved, messages=messages)
        result = response.message.content.strip()
        _track_usage(model=resolved)
        return result
    except Exception as exc:
        raise RuntimeError(f"Ollama generation failed ({resolved}): {exc}") from exc
```

Then update `ask_hybrid()` — in the `else` branch (simple complexity), wrap `ask_llm` to catch `OllamaOfflineError` and fall back to Claude. Replace the `else` block:

```python
    else:
        try:
            result = ask_llm(prompt, system=system, history=history)
            brain = "ollama"
        except OllamaOfflineError:
            # Ollama offline — silently route to Claude
            result = ask_claude(prompt, system=system, history=history)
            brain = "claude"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_brain_model_resolve.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Run full syntax check**

```bash
.venv/Scripts/python -m py_compile core/brain.py && echo OK
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add core/brain.py tests/test_brain_model_resolve.py
git commit -m "fix: Ollama model auto-detect with TTL cache + Claude fallback on offline"
```

---

## Task 2: Extend /api/system-status with Model Mismatch

**Files:**
- Modify: `dashboard/app.py` — `get_ollama_status()` function (line ~251)

The `/api/system-status` endpoint reads from `SystemMonitor.get_status()`, but `get_ollama_status()` already exposes `cfg_missing`. We need the status endpoint to surface it as a named issue so the frontend banner can list it.

- [ ] **Step 1: Read current `get_ollama_status()` return shape**

Currently returns:
```python
{
    "online": True,
    "models": ["mistral:latest"],
    "active": "mistral:latest",   # first available if configured missing
    "cfg_missing": True,          # True when env model not installed
}
```

- [ ] **Step 2: Add `issues` list to the status API response**

Find the `api_system_status` route (line ~1588). After `monitor.get_status()` call, inject a computed `issues` list:

Replace:
```python
@app.route("/api/system-status", methods=["GET"])
def api_system_status():
    """Get real-time system monitoring status."""
    monitor = _get_monitor()
    return jsonify(monitor.get_status()), 200
```

With:
```python
@app.route("/api/system-status", methods=["GET"])
def api_system_status():
    """Get real-time system monitoring status with computed issues list."""
    monitor = _get_monitor()
    status = monitor.get_status()

    issues = []
    ollama = get_ollama_status()
    if not ollama.get("online"):
        issues.append("Ollama offline")
    elif ollama.get("cfg_missing"):
        configured = os.getenv("OLLAMA_MODEL", "—")
        active = ollama.get("active", "unknown")
        issues.append(f"Ollama model mismatch: '{configured}' not installed, using '{active}'")

    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        issues.append("Claude API key not set (ANTHROPIC_API_KEY)")
    if not os.getenv("CRYPTOCOM_API_KEY", "").strip():
        issues.append("Crypto.com API key not set (CRYPTOCOM_API_KEY)")

    status["issues"] = issues
    return jsonify(status), 200
```

- [ ] **Step 3: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 4: Manual smoke test**

Start the app and hit the endpoint:
```bash
curl http://localhost:8080/api/system-status
```

Expected: JSON response with an `"issues"` array (may be empty if all configured correctly, or contain strings describing what's misconfigured).

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add computed issues list to /api/system-status"
```

---

## Task 3: Toast Notification System

**Files:**
- Modify: `dashboard/app.py` — `DASHBOARD_HTML` string

Adds a self-contained toast system: `#toast-container` div + `showToast(msg, type)` JS function. Three types: `'ok'` (green), `'warn'` (amber), `'err'` (red).

- [ ] **Step 1: Add `#toast-container` CSS to the `<style>` block in `DASHBOARD_HTML`**

Find the `</style>` closing tag inside `DASHBOARD_HTML` (search for `</style>` around line 930). Add before it:

```css
  /* ── Toast notifications ─────────────────────────────────────── */
  #toast-container{position:fixed;bottom:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;}
  .toast-msg{background:#141414;border:1px solid #333;border-radius:8px;padding:10px 16px;font-family:'Share Tech Mono',monospace;font-size:11px;color:#e0e0e0;opacity:0;transform:translateY(10px);transition:opacity 0.25s,transform 0.25s;pointer-events:auto;max-width:320px;word-break:break-word;}
  .toast-msg.show{opacity:1;transform:translateY(0);}
  .toast-msg.ok{border-color:#00ff8866;color:#00ff88;}
  .toast-msg.warn{border-color:#ffaa0066;color:#ffaa00;}
  .toast-msg.err{border-color:#ff445566;color:#ff8888;}
```

- [ ] **Step 2: Add `#toast-container` div to the HTML body in `DASHBOARD_HTML`**

Find the `</body>` closing tag near the end of `DASHBOARD_HTML`. Add immediately before it:

```html
<div id="toast-container"></div>
```

- [ ] **Step 3: Add `showToast()` JS function to the `<script>` block in `DASHBOARD_HTML`**

Find the closing `</script>` tag at the end of `DASHBOARD_HTML`. Add before it:

```javascript
// ── Toast notifications ──────────────────────────────────────────
function showToast(msg, type) {
  // type: 'ok' | 'warn' | 'err'
  const container = document.getElementById('toast-container');
  if (!container) return;
  // Max 3 toasts — drop oldest if needed
  while (container.children.length >= 3) {
    container.removeChild(container.firstChild);
  }
  const el = document.createElement('div');
  el.className = 'toast-msg ' + (type || 'ok');
  el.textContent = msg;
  container.appendChild(el);
  // Animate in
  requestAnimationFrame(() => {
    requestAnimationFrame(() => { el.classList.add('show'); });
  });
  // Auto-dismiss
  const delay = type === 'err' ? 8000 : 3000;
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
  }, delay);
}
```

- [ ] **Step 4: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 5: Manual smoke test**

Open the dashboard at `http://localhost:8080`. Open browser DevTools console and run:
```javascript
showToast('Test success', 'ok')
showToast('Test warning', 'warn')
showToast('Test error', 'err')
```

Expected: Three toasts appear bottom-right, correct colours, auto-dismiss.

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add toast notification system to dashboard"
```

---

## Task 4: Inline Command Status Bar

**Files:**
- Modify: `dashboard/app.py` — `DASHBOARD_HTML` string (command bar HTML + `runCmd()` JS)

Replaces the `alert()` calls in `runCmd()` with an inline `#cmd-status` bar below the command buttons.

- [ ] **Step 1: Add `#cmd-status` CSS to the `<style>` block**

Add after the `.cmd-btn` styles (around line 768) and before `</style>`:

```css
  /* ── Inline command status bar ──────────────────────────────── */
  #cmd-status{font-family:'Share Tech Mono',monospace;font-size:10px;padding:5px 12px;min-height:22px;color:var(--muted);transition:color 0.2s;letter-spacing:0.03em;}
  #cmd-status.running{color:var(--neon);}
  #cmd-status.ok{color:var(--neon);}
  #cmd-status.err{color:var(--red);}
```

- [ ] **Step 2: Add `#cmd-status` div to HTML, below the command bar**

Find the command bar block in `DASHBOARD_HTML` (around line 959–965):
```html
<div class="cmd-bar">
  ...
</div>
```

Add directly after the closing `</div>`:
```html
<div id="cmd-status"></div>
```

- [ ] **Step 3: Replace `runCmd()` to use status bar and `showToast()` instead of `alert()`**

Find `async function runCmd(btn,cmd)` (around line 1262). Replace the entire function with:

```javascript
async function runCmd(btn, cmd) {
  if (btn.disabled) return;
  btn.disabled = true;
  const tip = btn.querySelector('.tip');
  const original = tip ? tip.textContent : 'RUN';
  if (tip) tip.textContent = 'RUNNING';

  const bar = document.getElementById('cmd-status');
  if (bar) { bar.className = 'running'; bar.textContent = '\u25B6 Running ' + cmd + '...'; }

  try {
    const res = await fetch('/api/execute-command', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: cmd})
    });
    const data = await res.json();
    if (data.success) {
      if (tip) tip.textContent = 'DONE';
      if (bar) { bar.className = 'ok'; bar.textContent = '\u2713 ' + cmd + ' \u2014 done'; }
      // Summarise output in a toast (first 120 chars)
      const summary = (data.output || '').replace(/<[^>]+>/g, '').trim().slice(0, 120);
      if (summary) showToast(summary, 'ok');
      setTimeout(() => { if (bar) { bar.className = ''; bar.textContent = ''; } }, 5000);
    } else {
      if (tip) tip.textContent = 'ERR';
      const errMsg = data.error || res.statusText || 'unknown error';
      if (bar) { bar.className = 'err'; bar.textContent = '\u2717 ' + cmd + ' failed \u2014 ' + errMsg.slice(0, 80); }
      showToast(cmd + ' failed: ' + errMsg.slice(0, 100), 'err');
      setTimeout(() => { if (bar) { bar.className = ''; bar.textContent = ''; } }, 8000);
    }
  } catch (e) {
    if (tip) tip.textContent = 'ERR';
    const msg = 'Connection error \u2014 Is dashboard running?';
    if (bar) { bar.className = 'err'; bar.textContent = '\u2717 ' + msg; }
    showToast(msg, 'err');
    setTimeout(() => { if (bar) { bar.className = ''; bar.textContent = ''; } }, 8000);
  }
  setTimeout(() => { if (tip) tip.textContent = original; btn.disabled = false; }, 2000);
}
```

- [ ] **Step 4: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 5: Manual smoke test**

Open the dashboard. Click `/fng`. Verify:
- The `#cmd-status` bar shows `▶ Running /fng...` while running
- After completion, shows `✓ /fng — done` with a toast summary
- No `alert()` popup appears

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: replace alert() with inline command status bar + toast summary"
```

---

## Task 5: Config Health Banner

**Files:**
- Modify: `dashboard/app.py` — `DASHBOARD_HTML` string

Adds a dismissable amber banner at the top of the dashboard that lists config issues from `/api/system-status`.

- [ ] **Step 1: Add `#config-banner` CSS to the `<style>` block**

Add before `</style>`:

```css
  /* ── Config health banner ───────────────────────────────────── */
  #config-banner{display:none;background:#1a1200;border-bottom:1px solid #ffaa0055;padding:7px 16px;font-family:'Share Tech Mono',monospace;font-size:10px;color:#ffaa00;align-items:center;gap:12px;flex-wrap:wrap;}
  #config-banner.visible{display:flex;}
  #config-banner .cb-dismiss{margin-left:auto;cursor:pointer;color:#555;font-size:14px;line-height:1;background:none;border:none;padding:0;}
  #config-banner .cb-dismiss:hover{color:#ffaa00;}
```

- [ ] **Step 2: Add `#config-banner` div at the very top of the `<body>` in `DASHBOARD_HTML`**

Find the opening `<body>` tag in `DASHBOARD_HTML`. Add immediately after it:

```html
<div id="config-banner">
  <span>&#9888;</span>
  <span id="config-banner-text"></span>
  <button class="cb-dismiss" onclick="dismissConfigBanner()" title="Dismiss">&#x2715;</button>
</div>
```

- [ ] **Step 3: Add banner JS to the `<script>` block**

Add before the closing `</script>`:

```javascript
// ── Config health banner ─────────────────────────────────────────
async function loadConfigBanner() {
  if (sessionStorage.getItem('config-banner-dismissed') === '1') return;
  try {
    const res = await fetch('/api/system-status');
    if (!res.ok) return;
    const data = await res.json();
    const issues = data.issues || [];
    if (issues.length === 0) return;
    const banner = document.getElementById('config-banner');
    const text = document.getElementById('config-banner-text');
    if (!banner || !text) return;
    text.textContent = issues.length + ' issue' + (issues.length > 1 ? 's' : '') + ': ' + issues.join(' \u00B7 ');
    banner.classList.add('visible');
  } catch (e) {
    // Banner failure is non-critical — ignore
  }
}
function dismissConfigBanner() {
  const banner = document.getElementById('config-banner');
  if (banner) banner.classList.remove('visible');
  sessionStorage.setItem('config-banner-dismissed', '1');
}
// Fire on load
loadConfigBanner();
```

- [ ] **Step 4: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 5: Manual smoke test**

If `OLLAMA_MODEL` in `.env` is set to a model that isn't installed:
1. Open `http://localhost:8080`
2. Amber banner should appear at top listing the mismatch
3. Click `✕` — banner dismisses
4. Refresh page — banner reappears (sessionStorage clears on tab close, not refresh… actually `sessionStorage` persists through refresh but not tab close — this is expected behaviour)

If all config is correct: no banner appears.

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add dismissable config health banner on dashboard load"
```

---

## Task 6: Improve Holdings Error Card

**Files:**
- Modify: `dashboard/app.py` — `HOLDINGS_HTML` string (line ~2807)

The error is already rendered but shows raw API text. Make it actionable with specific guidance for error 10002.

- [ ] **Step 1: Replace the holdings error block in `HOLDINGS_HTML`**

Find (around line 2807):
```html
  {% if holdings_error %}
  <div style="background:#1a0000;border:1px solid #ff4455;border-radius:8px;padding:16px 20px;margin-bottom:20px;color:#ff8888;font-family:'Share Tech Mono',monospace;font-size:12px;">
    <div style="font-family:'Press Start 2P',monospace;font-size:8px;color:#ff4455;margin-bottom:8px;">&#9888; EXCHANGE CONNECTION ERROR</div>
    <div>{{ holdings_error[:120] }}</div>
    <div style="margin-top:10px;color:#555;font-size:11px;">Fix: In Crypto.com Exchange &rarr; API Management &rarr; Remove IP restriction on your key, or whitelist your PC&apos;s public IP.</div>
  </div>
  {% endif %}
```

Replace with:
```html
  {% if holdings_error %}
  <div style="background:#1a0000;border:1px solid #ff4455;border-radius:8px;padding:16px 20px;margin-bottom:20px;font-family:'Share Tech Mono',monospace;font-size:12px;">
    <div style="font-family:'Press Start 2P',monospace;font-size:8px;color:#ff4455;margin-bottom:10px;">&#9888; EXCHANGE ERROR</div>
    {% if '10002' in holdings_error or 'UNAUTHORIZED' in holdings_error %}
    <div style="color:#ff8888;">API key rejected (error 10002 — UNAUTHORIZED)</div>
    <ul style="color:#888;font-size:11px;margin-top:10px;padding-left:16px;line-height:1.8;">
      <li>Check <code>CRYPTOCOM_API_KEY</code> and <code>CRYPTOCOM_API_SECRET</code> in <code>.env</code></li>
      <li>In Crypto.com Exchange &rarr; API Management &rarr; confirm the key is active</li>
      <li>Remove IP restriction or whitelist your PC&apos;s public IP</li>
    </ul>
    {% else %}
    <div style="color:#ff8888;">{{ holdings_error[:160] }}</div>
    <div style="margin-top:10px;color:#555;font-size:11px;">Check your Crypto.com API credentials in .env and ensure the exchange is reachable.</div>
    {% endif %}
  </div>
  {% endif %}
```

- [ ] **Step 2: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "fix: improve holdings error card with specific 10002 guidance"
```

---

## Task 7: Global 500 Error Handler

**Files:**
- Modify: `dashboard/app.py` — add after security headers section (line ~70)

Ensures no route ever returns a raw Python traceback to the browser.

- [ ] **Step 1: Add error handlers after the `_security_headers` function**

Find the `@app.after_request` block (line ~56). Add after its closing lines, before the next `# ──` comment:

```python
@app.errorhandler(500)
def _handle_500(exc):
    """Return JSON for API routes, styled HTML for page routes."""
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Internal server error", "detail": str(exc)}), 500
    # Page route — render a styled error page
    _ERR_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>OpenClaw — Error</title>
<style>body{background:#0d0d0d;color:#e0e0e0;font-family:'Share Tech Mono',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.box{text-align:center;padding:40px;border:1px solid #ff4455;border-radius:12px;background:#141414;max-width:480px;}
.title{font-family:'Press Start 2P',monospace;font-size:12px;color:#ff4455;margin-bottom:16px;}
.msg{color:#888;font-size:11px;line-height:1.8;}
a{color:#00ff88;}</style></head>
<body><div class="box">
<div class="title">&#9888; SERVER ERROR</div>
<div class="msg">Something went wrong on this page.<br><br>
<a href="/">&#8592; Back to Dashboard</a></div>
</div></body></html>"""
    return _ERR_HTML, 500


@app.errorhandler(404)
def _handle_404(exc):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Not found"}), 404
    from flask import redirect
    return redirect("/")
```

- [ ] **Step 2: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add global 500/404 error handlers — no raw tracebacks"
```

---

## Task 8: Mobile CSS for New Components

**Files:**
- Modify: `dashboard/app.py` — `DASHBOARD_HTML` `<style>` block, mobile media query section

Extends the existing `@media (max-width: 560px)` block to cover the three new UI components.

- [ ] **Step 1: Find the existing mobile media query in `DASHBOARD_HTML`**

Search for `@media (max-width: 560px)` in `DASHBOARD_HTML` (around line 919). It currently contains `.cmd-btn` overrides.

- [ ] **Step 2: Add new component overrides inside the existing `@media (max-width: 560px)` block**

Find the closing `}` of that media query. Add before it:

```css
    /* Inline status bar — full width below buttons */
    #cmd-status{font-size:9px;padding:4px 8px;}
    /* Toast — centre-bottom on small screens */
    #toast-container{right:50%;transform:translateX(50%);bottom:12px;width:92vw;max-width:340px;}
    .toast-msg{max-width:100%;}
    /* Config banner — smaller font, wraps */
    #config-banner{font-size:9px;padding:6px 10px;}
```

- [ ] **Step 3: Verify syntax**

```bash
.venv/Scripts/python -m py_compile dashboard/app.py && echo OK
```

Expected: `OK`

- [ ] **Step 4: Manual mobile smoke test**

Open `http://localhost:8080` in browser DevTools with a 375px viewport (iPhone SE). Verify:
- Toast appears centred at bottom, not clipped off-screen
- Config banner text wraps cleanly
- Command status bar is readable

- [ ] **Step 5: Final full syntax check across all Python files**

```bash
cd C:/Users/ronsi95openclaw/Claude-openclaw
for f in $(find . -name "*.py" -not -path "./.venv/*" -not -path "./__pycache__/*"); do
  .venv/Scripts/python -m py_compile "$f" || echo "FAIL: $f"
done
echo "Syntax check complete"
```

Expected: `Syntax check complete` with no `FAIL:` lines.

- [ ] **Step 6: Run test suite**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Expected: All tests pass including the 5 new brain model resolution tests.

- [ ] **Step 7: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: extend mobile CSS for toast, status bar, config banner"
```

---

## Success Criteria Checklist

- [ ] ClawBot chat responds using available Ollama model (or Claude fallback if offline)
- [ ] No page returns a blank screen or Python traceback under any failure
- [ ] Command buttons show `▶ Running`, `✓ done`, or `✗ failed` inline — no `alert()` popups
- [ ] Toast appears after command execution with result summary
- [ ] Config banner appears when Ollama model mismatch exists; dismisses cleanly
- [ ] Holdings page shows actionable 10002 guidance instead of raw error string
- [ ] 500 errors return JSON for `/api/*` routes; styled page for page routes
- [ ] All new components render correctly at 375px viewport
- [ ] All Python files pass syntax check
- [ ] All tests pass
