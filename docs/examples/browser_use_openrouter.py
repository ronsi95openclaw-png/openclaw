#!/usr/bin/env python3
"""
Run a Browser Use task end-to-end with OpenRouter (e.g. NVIDIA Nemotron).

Browser Use has no dedicated ChatOpenRouter class — OpenRouter is an
OpenAI-compatible endpoint, so we drive it through ChatOpenAI by pointing
base_url at OpenRouter and passing an arbitrary model id.

Setup:
    python3 -m venv venv && . venv/bin/activate
    pip install "browser-use[core]"

Secrets — NEVER hardcode the key (see CLAUDE.md). Pass it via env:
    export OPENROUTER_API_KEY=sk-or-v1-...
    # Verify the exact model id at https://openrouter.ai/models?q=nemotron
    export OPENROUTER_MODEL='nvidia/llama-3.1-nemotron-70b-instruct'
    export BU_TASK='Go to https://news.ycombinator.com and return the title of the top story'

    python docs/examples/browser_use_openrouter.py

Notes:
  - Needs outbound network to openrouter.ai AND to whatever site the task
    visits. In a Claude-Code-on-the-web sandbox both must be in the network
    egress allowlist, or calls return 403 host_not_allowed.
  - Headless by default; set BU_HEADLESS=0 to watch the browser.
"""
import asyncio
import os
import sys

from browser_use import Agent, ChatOpenAI

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit("Set OPENROUTER_API_KEY in your environment first.")

MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
TASK = os.environ.get(
    "BU_TASK",
    "Go to https://news.ycombinator.com and return the title of the top story.",
)
HEADLESS = os.environ.get("BU_HEADLESS", "1") != "0"


async def main() -> None:
    llm = ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.2,
    )
    agent = Agent(task=TASK, llm=llm)
    history = await agent.run(max_steps=15)
    print("\n=== FINAL RESULT ===")
    print(history.final_result())


if __name__ == "__main__":
    asyncio.run(main())
