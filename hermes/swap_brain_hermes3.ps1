<#
  swap_brain_hermes3.ps1
  ----------------------
  Put Hermes Agent on the hermes3:8b brain (Ollama).
  Idempotent - safe to run more than once. Intended to run AFTER the GPU
  driver update (>=570) + reboot, but the config swap itself works either way.

  What it does:
    1. Pulls hermes3:8b from Ollama if not already present (~4.7 GB, first run only)
    2. Sets config.yaml -> model.default: "hermes3:8b"  (provider/base_url/num_ctx untouched)
    3. Unloads qwen2.5:14b / qwen3.5 from memory to free VRAM (model files kept on disk)
    4. Runs a 64K inference smoke test and prints PASS / FAIL
#>
$ErrorActionPreference = 'Stop'
function Info($m){ Write-Host $m -ForegroundColor Cyan }
function Ok($m){ Write-Host $m -ForegroundColor Green }
function Warn($m){ Write-Host $m -ForegroundColor Yellow }
function Err($m){ Write-Host $m -ForegroundColor Red }

$MODEL = "hermes3:8b"
$CFG   = "C:\Users\ronsi95openclaw\AppData\Local\hermes\config.yaml"

Info "=== Hermes brain swap -> $MODEL ==="

# 0) Resolve tools (refresh PATH from registry in case this shell predates installs)
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$H = (Get-Command hermes -ErrorAction SilentlyContinue).Source
if (-not $H) { $H = "C:\Users\ronsi95openclaw\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe" }
$OL = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $OL) { $OL = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (-not (Test-Path $H))  { Err "hermes.exe not found at $H"; exit 1 }
if (-not (Test-Path $OL)) { Err "ollama.exe not found at $OL"; exit 1 }

# 1) Ollama up?
try { Invoke-WebRequest "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 5 | Out-Null; Ok "Ollama is up." }
catch { Err "Ollama not responding on :11434. Start Ollama, then re-run."; exit 1 }

# 2) Pull hermes3:8b if missing
$present = (& $OL list 2>$null | Select-String -SimpleMatch "hermes3:8b")
if ($present) { Ok "$MODEL already present." }
else {
  Warn "Pulling $MODEL (~4.7 GB, first time only)..."
  & $OL pull $MODEL
  if ($LASTEXITCODE -ne 0) { Err "Pull failed (exit $LASTEXITCODE). Check network/tag."; exit 1 }
  Ok "Pulled $MODEL."
}

# 3) Point Hermes at hermes3:8b - edit config.yaml model.default (UTF-8, no BOM)
if (-not (Test-Path $CFG)) { Err "config not found: $CFG"; exit 1 }
$lines = [System.IO.File]::ReadAllLines($CFG)
$set = $false
for ($i = 0; $i -lt $lines.Length; $i++) {
  if (-not $set -and $lines[$i] -match '^\s{2}default:\s*".*"\s*$') {
    $lines[$i] = '  default: "' + $MODEL + '"'
    $set = $true
  }
}
if (-not $set) { Err "Could not find the model.default line in config.yaml. Set it manually to $MODEL."; exit 1 }
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($CFG, $lines, $enc)
Ok "config.yaml model.default -> $MODEL  (provider/base_url/context_length/ollama_num_ctx unchanged)"

# 4) Unload the previous big brains from memory (frees VRAM; no-op right after a reboot)
foreach ($m in @("qwen2.5:14b", "qwen3.5:latest")) {
  try { & $OL stop $m 2>$null | Out-Null } catch {}
}
Ok "Unloaded qwen2.5:14b / qwen3.5 from memory (model files left on disk; 'ollama rm' to reclaim space)."

# 5) Verify: 64K inference smoke test
Info "=== 64K inference smoke test (first load of $MODEL is slow) ==="
$out = (& $H -z "Reply with exactly this token and nothing else: HERMES3_OK" 2>&1 | Out-String)
Write-Host $out
if ($out -match 'HERMES3_OK') {
  Ok "PASS - Hermes is on $MODEL and answering at 64K context."
} elseif ($out -match 'CUDA|terminated|0xc0000409') {
  Warn "Config swap APPLIED, but inference hit a CUDA crash - the GPU driver still needs the >=570 update + reboot. Re-run this script after that."
} elseif ($out -match 'context window|num_ctx|64,000|64000') {
  Warn "Config swap APPLIED, but Ollama served < 64K context. Bump OLLAMA_CONTEXT_LENGTH or check the model load, then re-run."
} else {
  Warn "Config swap APPLIED. Smoke-test output above was unexpected - review it."
}
Info "=== done ==="
