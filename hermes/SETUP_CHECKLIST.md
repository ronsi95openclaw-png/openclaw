# Hermes - Setup Checklist (updated 2026-06-17)

The bot is fully built and configured. The ONLY thing left between here and a
working bot is the GPU driver + reboot. Everything keeps crashing until then.

## DONE
- [x] Hermes Agent v0.16.0 installed (`%LOCALAPPDATA%\hermes`)
- [x] Model **`hermes3:8b`** pulled + set; provider `ollama`; `base_url localhost:11434/v1`; 64K context override
- [x] Sub-agents, 3 skills (`ronsi95-os` / `lucid-rules` / `daily-briefing`), 8am-CT cron `09daa1d79310`
- [x] Dashboard adapter shows real status; `agents.json` Hermes enabled; `hermes\launch.py` + `start.bat`
- [x] **Telegram bot token set** (`hermes gateway setup` done)
- [x] **Gateway installed** with Windows Startup auto-launch (`Hermes_Gateway.cmd`)
- [x] **Bot locked to your chat** - `platforms.telegram.allowed_chats: ["6082698835"]` (only you can drive it)

## REMAINING (this is the whole blocker)
- [ ] **Finish the NVIDIA 610.47 install** (replaces the too-old 546.33 that crashes CUDA at 64K).
- [ ] **Reboot the PC.**
- [ ] After reboot, **verify** (a terminal):
      - `nvidia-smi` -> driver shows 610.47
      - `ollama run hermes3:8b "say hi"` -> fast reply on the GPU
      - the gateway auto-started (it's a Startup item) - check `hermes gateway status`
- [ ] **Message the bot on Telegram** (e.g. "status" or "crypto") -> should reply fast now.
- [ ] **Restart the dashboard** (process on `127.0.0.1:8080`) so it loads the new Hermes adapter,
      then the Hermes card shows **LIVE**.

## If it still misbehaves after reboot
- Re-run `hermes\swap_brain_hermes3.cmd` (idempotent: confirms model + 64K self-test).
- If Ollama isn't up: it auto-starts with Windows; otherwise `ollama serve`.
- Hermes keeps its own secrets in `%LOCALAPPDATA%\hermes\.env` - it does NOT read ClawBot's keys.

## Why it wasn't working before the reboot
`hermes3:8b` at the 64K context Hermes requires ran on the **546.33 CUDA backend**, which
crashes (`0xc0000409`). That killed Ollama, which took the gateway down with it - one slow
(~47s) reply, then collapse. The 610.47 driver is the fix.
