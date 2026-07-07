# Hermes -> OpenRouter (cloud brain) - 24/7 setup

**Why:** Local `hermes3:8b` on Ollama crashes CUDA (0xc0000409) at 64K context on
driver 546.33. OpenRouter runs in the cloud, so it sidesteps the GPU/driver blocker
entirely - Hermes can be fully operational 24/7 *without* the driver update or reboot.

Plan: OpenRouter = primary brain. Keep Ollama/hermes3:8b as a documented fallback for
after the driver fix (swap back with `hermes\swap_brain_hermes3.ps1`).

---

## STEP 1 - Put your OpenRouter key in Hermes' OWN secrets file (NOT the project .env, NOT chat)

Open: `%LOCALAPPDATA%\hermes\.env`  (this file is Hermes-only; never the repo root .env)

Add the line:

    OPENROUTER_API_KEY=sk-or-...your key...

Save. Do not paste the key anywhere else, and never commit it.

---

## STEP 2 - Point Hermes at OpenRouter (run this in VS Code with Claude Code)

Hermes' config lives at `%LOCALAPPDATA%\hermes\config.yaml` (outside the repo, so Claude
in Cowork can't see it). Paste this prompt to Claude Code / Claude in VS Code on your PC:

----------------------------------------------------------------------
CONTEXT: Switch the Hermes Agent (Hermes Agent v0.16.0) from the local Ollama brain to
an OpenRouter cloud model, keeping Ollama as a fallback. Hermes config is at
%LOCALAPPDATA%\hermes\config.yaml ; Hermes secrets at %LOCALAPPDATA%\hermes\.env
(OPENROUTER_API_KEY is already set there). Do NOT touch the repo root .env.

DO THIS:
1. Read %LOCALAPPDATA%\hermes\config.yaml and show me the current model/provider block.
2. Back it up to config.yaml.pre-openrouter-bak.
3. Run `hermes --help` and `hermes models` (or check Hermes docs) to confirm the exact
   keys this version uses for an OpenAI-compatible / OpenRouter provider. OpenRouter is
   OpenAI-compatible: base_url = https://openrouter.ai/api/v1 , api_key from
   OPENROUTER_API_KEY, and the model id is an OpenRouter slug.
4. Edit config.yaml to set:
     - provider  -> the OpenAI-compatible/openrouter provider this version supports
     - base_url  -> https://openrouter.ai/api/v1
     - api key   -> read from OPENROUTER_API_KEY (env), do not hardcode
     - model.default -> a current FREE model slug (verify it is live at
       https://openrouter.ai/models?max_price=0 ). Good candidates to check:
         meta-llama/llama-3.3-70b-instruct:free
         deepseek/deepseek-r1:free
         google/gemini-2.0-flash-exp:free
       Pick one that is currently available and supports tool/function calling.
     - drop or relax the 64K num_ctx override that was needed only for local Ollama.
5. Smoke test: `hermes -z "Reply with exactly: HERMES_OR_OK"` -> expect HERMES_OR_OK fast.
   If it errors on the model slug, list free models and pick another.
6. Print a one-line diff of what changed in config.yaml. Never print the API key.

CONSTRAINTS: surgical edits only, keep YAML formatting, back up first, no secrets in
output, stop and show me if the schema is unclear.
----------------------------------------------------------------------

NOTE on free models: free tiers are rate-limited and can be deprecated. For reliable 24/7
use, if you hit limits, switch model.default to a cheap paid slug (e.g. a Llama/Qwen
instruct at ~$0.1-0.5 / Mtok). Same config, just a different model id.

---

## STEP 3 - 24/7 always-on

Hermes already installs a Windows login-startup gateway (Hermes_Gateway.cmd) per
hermes\SETUP_CHECKLIST.md. Confirm it:

    hermes gateway status        # should show running
    # if not installed as a service:
    hermes gateway install       # registers the login-startup service
    # OR run manually:  hermes\start.bat   (writes hermes\hermes.pid)

Because OpenRouter is cloud, the gateway no longer depends on Ollama being healthy, so it
should stay up across reboots without the driver fix.

---

## STEP 4 - Verify end-to-end

1. Telegram: message @Ronsi95hermesbot  ("status" or "/briefing") -> fast reply.
2. Restart the dashboard so the Hermes card re-polls and flips to LIVE:
       infra\restart_dashboard.bat
   Hermes card detection: pidfile %LOCALAPPDATA%\hermes\gateway.pid (or hermes\hermes.pid)
   or any hermes.exe in tasklist.
3. Done -> M3 (NVIDIA driver) is no longer a blocker for Hermes; it only matters if/when
   you want to move back to the local hermes3:8b brain later.
