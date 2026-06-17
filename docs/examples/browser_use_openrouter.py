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
    # Copy the EXACT slug from the model's OpenRouter page (the ':free' suffix
    # matters — free and paid variants are different slugs). For agentic browser
    # tasks pick a general chat model, e.g. Nemotron 3 Ultra; avoid the rerank/
    # embed/content-safety variants (not chat models).
    export OPENROUTER_MODEL='nvidia/nemotron-3-ultra:free'  # verify on OpenRouter
    export BU_TASK='Go to https://news.ycombinator.com and return the title of the top story'

    python docs/examples/browser_use_openrouter.py

Notes:
  - Needs outbound network to openrouter.ai AND to whatever site the task
    visits. In a Claude-Code-on-the-web sandbox both must be in the network
    egress allowlist, or calls return 403 host_not_allowed.
  - Headless by default; set BU_HEADLESS=0 to watch the browser.
  - Free-tier OpenRouter models are rate-limited; an agentic run makes many
    calls per task, so a multi-step browse may hit throttling.
"""
import asyncio
import os
import sys

from browser_use import Agent, ChatOpenAI

API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not API_KEY:
    sys.exit("Set OPENROUTER_API_KEY in your environment first.")

# Default to a general-purpose Nemotron chat model; override with the exact
# slug from your OpenRouter workspace (e.g. nvidia/nemotron-3-super:free).
MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra:free")
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
