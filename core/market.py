"""CoinGecko market data + LLM analysis for ClawBot.

Fetches BTC and ETH prices from the CoinGecko free API (no key required)
and asks the brain for a brief market outlook.

Usage:
    from core.market import get_market_summary
    summary = get_market_summary()
"""
from __future__ import annotations

import requests

from core.brain import ask_hybrid

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin,ethereum,solana"
    "&vs_currencies=usd"
    "&include_24hr_change=true"
    "&include_market_cap=true"
)

_ANALYSIS_PROMPT = """You are ClawBot, a sharp crypto analyst for Ronnie.

Current prices:
{price_block}

Give a brief (3-5 bullets) market outlook:
- Overall sentiment (bullish/bearish/neutral)
- Key observation per coin
- One actionable insight

Be direct and concise. No waffle."""


def _fetch_prices() -> dict:
    """Fetch live prices from CoinGecko. Raises on network error."""
    resp = requests.get(COINGECKO_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _format_price_block(data: dict) -> str:
    lines = []
    labels = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
    for coin_id, label in labels.items():
        coin = data.get(coin_id, {})
        price = coin.get("usd", 0)
        change = coin.get("usd_24h_change", 0) or 0
        arrow = "+" if change >= 0 else ""
        lines.append(f"  {label}: ${price:,.2f}  ({arrow}{change:.2f}% 24h)")
    return "\n".join(lines)


def get_market_summary() -> str:
    """Return a formatted market summary with LLM analysis.

    Returns:
        Multi-line string ready to send as a Telegram message.

    Raises:
        requests.RequestException: If CoinGecko is unreachable.
        RuntimeError: If LLM analysis fails.
    """
    data = _fetch_prices()
    price_block = _format_price_block(data)

    prompt = _ANALYSIS_PROMPT.format(price_block=price_block)
    analysis, brain = ask_hybrid(prompt, force="simple")   # use Ollama — quick task

    # Strip Markdown bold/italic that Ollama sometimes emits (**text**, *text*)
    import re
    analysis = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', analysis)
    analysis = re.sub(r'\*(.+?)\*', r'<i>\1</i>', analysis)

    lines = [
        "📊 <b>Market Update</b>",
        "",
        "<b>Live Prices:</b>",
        price_block,
        "",
        f"<b>ClawBot Analysis</b> <i>(via {brain})</i>:",
        analysis,
    ]
    return "\n".join(lines)
