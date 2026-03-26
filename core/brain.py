"""LLM interface using Ollama for local model inference.

This module provides a single function `ask_llm(prompt)` which attempts to
call the local `ollama` CLI to generate a response from the configured
model (environment variable `OLLAMA_MODEL` or `qwen2.5:14b`).

It falls back to raising a clear error if the `ollama` executable is not
available. Keep Ollama (the server/CLI) installed and running on the host
for this to work.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional


DEFAULT_MODEL = "qwen2.5:14b"


def ask_llm(prompt: str, model: Optional[str] = None, timeout: int = 60) -> str:
    """Ask the local Ollama model for a textual response.

    This implementation invokes the `ollama` CLI (recommended workflow for
    local Ollama installs). It returns the raw stdout as the response.

    Args:
        prompt: The user prompt to send to the model.
        model: Optional model identifier, will read `OLLAMA_MODEL` env var
            or use the module default.
        timeout: Seconds to wait for the CLI to complete.

    Returns:
        The model's textual response.

    Raises:
        RuntimeError: If the `ollama` CLI is not found or generation fails.
    """
    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)

    # Build command: `ollama run <model> <prompt>`
    cmd = ["ollama", "run", model, prompt]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "'ollama' CLI not found. Install Ollama or ensure 'ollama' is on PATH"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Ollama generation timed out") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"Ollama generation failed: {stderr}")

    return proc.stdout.strip()
