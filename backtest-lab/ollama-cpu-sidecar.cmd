@echo off
REM ============================================================================
REM CPU-only Ollama sidecar for backtest-lab  --  listens on 127.0.0.1:11435
REM ----------------------------------------------------------------------------
REM WHY: the shared GPU daemon on :11434 is broken (driver 546.33 too old for
REM Ollama 0.30.4's CUDA 12.8 runtime -> "device kernel image is invalid").
REM Forcing CPU here sidesteps the broken CUDA runner entirely, so the lab can
REM run WITHOUT a reboot and WITHOUT touching the shared daemon or the live bot.
REM
REM Isolation guarantees:
REM   * Different port (11435) -> shared :11434 daemon untouched.
REM   * CUDA_VISIBLE_DEVICES=-1 -> no GPU visible -> pure CPU, no crash.
REM   * Reuses existing model blobs (no re-download).
REM   * Single model / single request -> modest footprint on the shared box.
REM
REM NOTE: CPU inference of qwen2.5:14b is slow (~2-5 tok/s). For faster lab work
REM       point LANGCHAIN_MODEL_NAME at llama3:latest (8B) in .vibe-trading\.env.
REM ============================================================================
setlocal
set "OLLAMA_HOST=127.0.0.1:11435"
set "CUDA_VISIBLE_DEVICES=-1"
set "OLLAMA_LLM_LIBRARY=cpu"
set "OLLAMA_MODELS=C:\Users\ronsi95openclaw\.ollama\models"
set "OLLAMA_NUM_PARALLEL=1"
set "OLLAMA_MAX_LOADED_MODELS=1"
set "OLLAMA_KEEP_ALIVE=30m"
"C:\Users\ronsi95openclaw\AppData\Local\Programs\Ollama\ollama.exe" serve
endlocal
