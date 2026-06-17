# External Tool Evaluations

Evaluation of GitHub tools surfaced via "Comment SEND / RED PILL for the link"
social-media reels, requested for install + test on 2026-06-17.

> ⚠️ **Sourcing note.** All of these came from viral "comment a keyword and I'll
> DM you the link" reels — a common malware/typosquat distribution channel. Each
> was audited before any install. This repo holds exchange + Anthropic + Telegram
> credentials in `.env`, so anything that runs install hooks here is treated as
> high-risk until proven clean. Installs/tests below were performed in a
> disposable scratch dir (`/home/user/tooltest`), not in this repo.

## Summary

| Tool | Repo | Verdict | Status |
|------|------|---------|--------|
| Open LLM VTuber | `Open-LLM-VTuber/Open-LLM-VTuber` | ✅ Legit (11.5k★) | Installed + tested OK |
| Browser Use | `browser-use/browser-use` | ✅ Legit (99.2k★) | Installed + tested OK |
| Claude Ads | `AgriciDaniel/claude-ads` | ✅ Clean | Installed + tested OK |
| Camofox Browser | `jo-inc/camofox-browser` | 🛑 **Rejected** | Not installed — security |
| Hyperframes | `heygen/hyperframes` | ⚪ Does not exist | 404 |

## 🛑 Camofox Browser — REJECTED, do not install

`jo-inc/camofox-browser` is **not** the real Camoufox (`daijro/camoufox`); it is a
wrapper branded specifically as an **"OpenClaw plugin"** (keywords: `openclaw`,
`clawdbot`, `moltbot`; ships `openclaw.plugin.json` + `plugin.ts`) and distributed
through "comment SEND" reels.

The disqualifying finding: its auto-run `npm` `postinstall` script and `scripts/exec.js`
contain code **deliberately written to evade OpenClaw's own security scanner.** The
authors' own comments say so:

- `scripts/postinstall.js:169` — *"Dynamic import with renamed binding to avoid
  triggering static code scanners (e.g. OpenClaw plugin security) that pattern-match
  on child_process function names like spawn/spawnSync/exec/execSync…"*
- `scripts/exec.js:1` — *"Isolated so that caller files don't contain the
  'child_process' module name, avoiding OpenClaw scanner 'dangerous-exec'…"*

It also runs a subprocess-spawning postinstall hook and enables outbound telemetry
to `camofox-telemetry.askjo.workers.dev` by default. Regardless of whether today's
downloaded payload is benign, a package engineered to bypass this project's security
scanning is a supply-chain risk and was not installed.

## ✅ Open LLM VTuber

- **Install:** `uv sync` (Python 3.10–3.12; pulled torch 2.10, onnxruntime, sherpa-onnx, etc.).
  Needed `UV_HTTP_TIMEOUT=300` to survive a slow `pydantic-core` download.
- **Config:** `cp conf.default.yaml conf.yaml`.
- **Test:** `python run_server.py` booted fully — initialized Live2D (`mao_pro`),
  downloaded + loaded the SenseVoice ASR model, and bound Uvicorn on
  `http://localhost:12393`. `GET /` → **HTTP 200** serving the `Open-LLM-Vtuber` web UI.
- **Notes:** Optional MCP tool servers (`time`, `ddg-search`) log non-fatal connection
  errors when `uvx`/network is unavailable; startup continues. Needs an LLM backend
  (Ollama/OpenAI/etc.) configured in `conf.yaml` for actual conversation, and `ffmpeg`
  for audio.

## ✅ Browser Use

- **Install:** `pip install "browser-use[core]"` → `browser-use==0.13.1` (Python 3.11+).
- **Test:** `from browser_use import Agent` imports; `browser-use doctor` →
  package / browser profile / network checks pass (optional `cloudflared` + `profile-use`
  not installed). CLI exposes the full subcommand set.
- **Notes:** Needs an LLM API key (and a browser profile) to actually drive a browser.

## ✅ Claude Ads

- **What it is:** a Claude Code *skill* bundle (1 orchestrator + 22 sub-skills + 10 agents)
  for auditing paid-ad accounts.
- **Audit:** installer (`install.sh`) is defensively written (path-injection / flag-confusion
  guards, target whitelist). Two caveats: it clones `AI-Marketing-Hub/claude-ads`
  (not the `AgriciDaniel/claude-ads` from the reel) and suggests a `curl|bash` for an
  optional "banana-claude" add-on. Installed from the **locally audited checkout** instead,
  and skipped the `curl|bash` step.
- **Install:** skill/agent markdown → `~/.claude/skills` + `~/.claude/agents`;
  Python deps (`requests`, `playwright`, `Pillow`, `reportlab`, `matplotlib`) into a venv.
- **Test:** all 6 scripts import with real deps; the `validate_url` SSRF guard correctly
  **blocks** `localhost`, the cloud-metadata IP `169.254.169.254`, loopback, and non-HTTP
  schemes while allowing public URLs. All 23 `ads*` skills + 10 agents register in Claude Code.
- **Notes:** image-generation sub-skills (`/ads generate`, `/ads photoshoot`) need the
  separate `banana-claude` package, which was not installed.

## Reproduction

Scratch installs lived in `/home/user/tooltest/` (ephemeral container — not committed).
Re-run from clean clones:

```bash
# Open LLM VTuber
git clone --depth 1 https://github.com/Open-LLM-VTuber/Open-LLM-VTuber
cd Open-LLM-VTuber && UV_HTTP_TIMEOUT=300 uv sync && cp conf.default.yaml conf.yaml && uv run python run_server.py

# Browser Use
python3 -m venv venv && . venv/bin/activate && pip install "browser-use[core]" && browser-use doctor

# Claude Ads (audit install.sh first; it clones AI-Marketing-Hub/claude-ads)
git clone --depth 1 https://github.com/AgriciDaniel/claude-ads && cd claude-ads && less install.sh
```
