# OpenClaw Dashboard — App-Quality Pass Design
**Date:** 2026-04-19  
**Status:** Approved  
**Scope:** Full functional + UX pass on `dashboard/app.py` and `core/brain.py`  
**User:** Ronnie only (personal command centre, localhost)

---

## Goal

Make the OpenClaw dashboard fully functional as a daily-use app. Every button works, every failure is handled gracefully, and the UI gives clear feedback for every action. No blank pages, no silent failures, no raw tracebacks.

---

## Section 1 — Chat: Ollama Auto-Detect + Claude Fallback

### Problem
`core/brain.py` reads `OLLAMA_MODEL` from `.env` and uses it without checking if the model is installed. If it isn't (e.g. `qwen2.5:14b` configured but only `mistral:latest` installed), the chat endpoint returns no response.

### Fix
In `ask_ollama()` in `core/brain.py`:

1. Call `ollama.list()` at the start of each request to get installed models.
2. Resolution order:
   - If `OLLAMA_MODEL` env var is set **and** installed → use it.
   - Else → use `models[0]` (first available, e.g. `mistral:latest`).
   - If `ollama.list()` raises (Ollama offline) → raise `OllamaOfflineError`.
3. `ask_hybrid()` already has a Claude fallback path — ensure it catches `OllamaOfflineError` and routes to Claude API cleanly.
4. Cache the resolved model name in a module-level variable with a timestamp. Re-check via `ollama.list()` only if cache is older than 60 seconds (lazy TTL — no background thread).

### Result
Chat works with whatever Ollama model is installed. If Ollama goes offline, messages silently route to Claude. Zero config changes required.

---

## Section 2 — Error Handling: No Blank Pages, No Tracebacks

### Policy
Every route that fetches external data (exchange API, Ollama, file reads) must:
- Wrap the fetch in `try/except`.
- On failure, render a styled error state — not a blank page, not a Python traceback.
- Error cards: amber `⚠` icon, plain-English message, one actionable hint.

### Per-Page Spec

| Page | Failure scenario | Error message | Hint |
|------|-----------------|---------------|------|
| `/holdings` | API error 10002 | "Crypto.com API key invalid" | "Update CRYPTOCOM_API_KEY in .env" |
| `/portfolio` | No trade data | Empty state card | "Run /scan or /autotrade to start" |
| `/clip-economy` | No income logged | Shows estimates with `est.` badge | Already implemented ✅ |
| `/taskboard` | Fetch fails | Shows empty columns (no crash) | Already implemented ✅ |
| `/team` | team.json missing | Renders default agent list | Already implemented ✅ |
| `/status` | Any subsystem down | Per-card red indicator | Existing behaviour — extend as needed |
| All `/api/*` routes | Unhandled exception | `{"ok": false, "error": "..."}` JSON | Never return 500 with HTML traceback |

### Implementation Note
Flask's `@app.errorhandler(500)` should return JSON for API routes and a styled HTML page for page routes. Add a global error handler in `dashboard/app.py`.

---

## Section 3 — Feedback: Inline Command Bar + Toast Notifications

### Inline Status Bar
- Location: slim bar directly below the 7 command buttons (`/scan`, `/market`, etc.)
- States:
  - **Idle:** empty / hidden
  - **Running:** `▶ Running /scan...` with a pulsing dot
  - **Success (5s then fade):** `✓ /scan — 3 signals found`
  - **Error (8s then fade):** `✗ /scan failed — Exchange offline`
- Implemented in the existing `<script>` block in `DASHBOARD_HTML`. The command button `onclick` sets the bar state, awaits the `/api/execute-command` response, updates to success or error.

### Toast Notifications
- Location: bottom-right corner, stacked vertically, z-index above everything.
- Trigger sources:
  - Background polling of `/api/system-status` every 30 seconds from the dashboard JS. If agent status changes between polls, a toast fires.
  - NEW AGENT deployed successfully.
  - Chat routed to Claude (subtle info toast: "Ollama offline — using Claude").
- Behaviour: auto-dismiss after 3 seconds. Max 3 visible at once (oldest drops off). Stackable.
- Colours: green = success, amber = warning/info, red = error.
- Implementation: self-contained `<div id="toast-container">` + `showToast(msg, type)` JS function added to `DASHBOARD_HTML`. No external library.

---

## Section 4 — Config Health Banner

### Behaviour
- On dashboard load, JS calls `GET /api/system-status` (already exists).
- If response contains any `status: "error"` or `status: "warn"` entries → render amber banner at very top of page.
- Banner format: `⚠  2 issues: Ollama model mismatch · Crypto.com API key invalid  [✕]`
- Dismiss: clicking `✕` hides the banner for the session (`sessionStorage.setItem('banner-dismissed', '1')`). Reappears on next full page load if issues still exist.
- All clear (no issues): banner not rendered, no empty space.

### Issues Detected
The existing `/api/system-status` endpoint already checks:
- Ollama online/offline
- Claude API key set
- Crypto.com API key set
- Auto-trade enabled/disabled

Extend it to also report:
- Ollama model mismatch (configured model not in installed list)

---

## Section 5 — Mobile: Extend New Components

### Existing Mobile Support
A full mobile layout pass already exists (commit `e7edaf2`). The existing breakpoints and layouts are preserved.

### New Component Mobile Behaviour

| Component | Mobile behaviour |
|-----------|-----------------|
| Inline status bar | Wraps below command buttons, full width |
| Toast notifications | Anchored bottom-centre (not bottom-right) on `max-width: 560px` |
| Config health banner | Single line, smaller font, scrollable if text overflows |
| NEW AGENT modal | Already mobile-responsive (existing implementation) |

No new breakpoints needed — extend existing `@media (max-width: 560px)` blocks.

---

## Out of Scope

- Holdings page API credentials fix (config issue, not code).
- New agent capabilities or new Telegram commands.
- Redesigning existing page layouts.
- Multi-user auth or access control.
- Whisper voice handler end-to-end wiring.

---

## Files Changed

| File | Changes |
|------|---------|
| `core/brain.py` | Model auto-detect + `OllamaOfflineError`, Claude fallback fix |
| `dashboard/app.py` | Inline status bar, toast system, config banner, error handlers, `/api/system-status` extension |

No new files required. No new dependencies.

---

## Success Criteria

- [ ] ClawBot chat responds using available Ollama model (or Claude fallback)
- [ ] No page returns a blank screen or Python traceback under any failure
- [ ] Command buttons show running/success/error state inline
- [ ] Toast appears when agent state changes or background event fires
- [ ] Config banner appears when Ollama model mismatch exists, dismisses cleanly
- [ ] All new components render correctly on mobile (560px viewport)
- [ ] All 36 Python files pass syntax check after changes
