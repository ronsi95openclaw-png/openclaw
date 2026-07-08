# Reboot Runbook — NVIDIA driver update (Ollama GPU fix)

**Why:** Driver 546.33 (CUDA 12.3) is too old for Ollama 0.30.4's bundled CUDA 12.8 runtime →
`CUDA error: device kernel image is invalid`. Fix = update NVIDIA driver to **≥ 570.x**, reboot.
Created 2026-06-13. Survives the reboot (read this file again afterward if needed).

---

## 1. Install the driver (you do this on the box)

**Easiest — NVIDIA App (or GeForce Experience):**
- Open NVIDIA App → **Drivers** → it auto-detects *GeForce RTX 2070 SUPER* → **Download** latest
  Game Ready Driver → **Express install**.

**Manual fallback:** https://www.nvidia.com/Download/index.aspx
- Product Type: **GeForce** · Series: **GeForce RTX 20 Series** · Product: **GeForce RTX 2070 SUPER**
- OS: **Windows 11** · Download Type: **Game Ready Driver** → Search → Download → run → **Express install**.

- Requirement: **driver ≥ 570.x** (any current mid-2026 build, e.g. 576/580+, is fine).
- Prefer **Express** install (not "clean install") — it preserves the rollback option.
- **Reboot** when prompted.

**Rollback if anything goes wrong:** Device Manager → Display adapters → *RTX 2070 SUPER* →
Properties → Driver → **Roll Back Driver** → returns to 546.33.

---

## 2. What comes back automatically after reboot

All via the user **Startup folder** (`shell:startup`) — these fire on **interactive login**:
| Component | Launches | Notes |
|---|---|---|
| Ollama | `ollama serve` | daemon on 127.0.0.1:11434 |
| ClawBot Dashboard | `infra\start_dashboard.bat` | Flask dashboard |
| ClawBot Receiver | `infra\start_clawbot.bat` | Telegram bot |
| HaulYeah | (its own autostart) | out of scope, ignore |

Scheduled tasks (fire on their own triggers): `ClawBot-Watchdog` (timed → `run_watchdog.bat`),
`ClawBot-LiquiditySweep-Watch` (daily → `paper_watch_run.bat`).

## ⚠️ 3. CRITICAL: you must LOG IN after reboot

`AutoAdminLogon = 0` → the box boots to the **lock screen** and **nothing above starts until
someone logs into Windows.** Remote path is fine: **AnyDesk Service** + **Tailscale** are
Automatic services (up at boot, pre-login) → connect, reach the login screen, **log in** →
Startup apps launch ~immediately.

Do NOT walk away after the reboot assuming the bot is back — confirm the login happened.

---

## 4. Post-reboot verification (ping me "rebooted" and I'll run these)

```powershell
# a) driver bumped to >= 570
& "C:\Windows\System32\nvidia-smi.exe" --query-gpu=driver_version,name,memory.total,memory.free --format=csv,noheader

# b) Ollama inference actually works now (expect a normal reply, HTTP 200, no 0xc0000409 crash)
curl.exe -s -w "`n[HTTP %{http_code}]`n" http://localhost:11434/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{\"model\":\"qwen2.5:14b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with one word: PONG\"}],\"max_tokens\":10}'

# c) live bot back? (expect ollama + the two ClawBot venv python procs)
Get-Process ollama,python | Select-Object Id,ProcessName,Path
```

If (a) shows ≥570 and (b) returns a reply with HTTP 200, the GPU fix worked → proceed to the
vibe-trading backtests (Step 3) via `backtest-lab\vt.cmd`.

## 5. Perf note
`qwen2.5:14b` Q4 (~9 GB) > 8 GB VRAM → partial CPU offload even after the fix (runs, not max speed).
If you want it fully on-GPU later: a smaller quant (Q4_K_S) or `qwen2.5:7b`.
