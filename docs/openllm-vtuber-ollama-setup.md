# Open LLM VTuber → OpenClaw Ollama setup

Wires the [Open LLM VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)
desktop companion to the **same local Ollama brain OpenClaw uses**
(`qwen2.5:14b` at `localhost:11434`, per `core/brain.py` / `.env` `OLLAMA_MODEL`).

Verified on 2026-06-17: with this config the server's own `validate_config`
passes and it boots and serves the web UI on `http://localhost:12393`. Actual
chat requires Ollama running with the model pulled.

## 1. Install

```bash
git clone --depth 1 https://github.com/Open-LLM-VTuber/Open-LLM-VTuber
cd Open-LLM-VTuber
UV_HTTP_TIMEOUT=300 uv sync          # high timeout: pydantic-core download is slow
cp conf.default.yaml conf.yaml
```

## 2. Point it at OpenClaw's Ollama

The default `conf.yaml` already chooses Ollama
(`conversation_agent_choice: basic_memory_agent` → `llm_provider: 'ollama_llm'`).
Only the model needs to match OpenClaw. Under `character_config.agent_config.llm_configs`:

```yaml
      ollama_llm:
        base_url: 'http://localhost:11434/v1'
        model: 'qwen2.5:14b'   # match OpenClaw's OLLAMA_MODEL (core/brain.py)
        temperature: 1.0
```

Make sure the model is available to Ollama (same one OpenClaw expects):

```bash
ollama pull qwen2.5:14b
ollama serve        # if not already running
```

## 3. Run

```bash
ollama serve &                       # OpenClaw's brain — must be up for chat
UV_HTTP_TIMEOUT=300 uv run python run_server.py
# open http://localhost:12393
```

First boot downloads the SenseVoice ASR model (~1 GB) into `./models/`.

## Notes / gotchas

- **`ffmpeg`** is needed for audio (pydub warns if missing): `apt-get install ffmpeg`.
- **MCP tool servers**: the default enables `time` + `ddg-search`
  (`agent_settings.basic_memory_agent.mcp_enabled_servers`). They spawn via `uvx`
  and log non-fatal `Connection closed` errors if `uvx`/network is unavailable —
  startup continues regardless. Set `use_mcpp: False` to silence them if you don't
  need tool use.
- VTuber and OpenClaw both hit Ollama on `localhost:11434`; running both at once
  just means two clients against the same Ollama — fine, but they share GPU/CPU and
  the model's keep-alive.
- This is a **separate desktop app**, not wired into OpenClaw's process. It does not
  touch OpenClaw's Telegram/exchange/Anthropic credentials.
```
