"""Scan prediction market platforms and rank tradeable opportunities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, stdev
from typing import Any, Dict, List, Optional


@dataclass
class MarketOpportunity:
    platform: str
    market_id: str
    title: str
    side: str
    price: float
    probability: float
    volume_24h: float
    spread: float
    time_to_expiry_days: float
    liquidity_score: float
    confidence: float
    metadata: Dict[str, Any]


def fetch_polymarket_markets(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a raw list of Polymarket market data from the discovery API."""
    # TODO: implement Polymarket API discovery using REST or WebSocket endpoints.
    return []


def fetch_kalshi_markets(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a raw list of Kalshi market data from the discovery API."""
    # TODO: implement Kalshi API discovery and market filtering.
    return []


def is_tradeable_market(raw_market: Dict[str, Any]) -> bool:
    """Filter markets by liquidity, volume, expiry, and price action."""
    volume = raw_market.get("volume_24h", 0)
    time_to_expiry = raw_market.get("time_to_expiry_days", 999)
    spread = raw_market.get("spread", 1.0)

    if volume < 200:
        return False
    if time_to_expiry > 30:
        return False
    if spread > 0.05:
        return False

    return True


def score_market(raw_market: Dict[str, Any]) -> float:
    """Score a market for ranking by opportunity and liquidity."""
    volume = float(raw_market.get("volume_24h", 0))
    spread = float(raw_market.get("spread", 1.0))
    time_to_expiry = float(raw_market.get("time_to_expiry_days", 999))
    price = float(raw_market.get("price", 0.5))

    volume_score = min(volume / 1000, 1.0)
    spread_score = max(0.0, 1.0 - spread * 10)
    expiry_score = max(0.0, 1.0 - time_to_expiry / 30)
    price_score = 1.0 - abs(price - 0.5) * 2

    return mean([volume_score, spread_score, expiry_score, price_score])


def rank_markets(raw_markets: List[Dict[str, Any]]) -> List[MarketOpportunity]:
    """Return ranked market opportunities after filtering and scoring."""
    opportunities: List[MarketOpportunity] = []
    for market in raw_markets:
        if not is_tradeable_market(market):
            continue

        opportunity = MarketOpportunity(
            platform=market.get("platform", "unknown"),
            market_id=market.get("market_id", ""),
            title=market.get("title", ""),
            side=market.get("side", "yes"),
            price=float(market.get("price", 0.5)),
            probability=1 - float(market.get("price", 0.5)),
            volume_24h=float(market.get("volume_24h", 0)),
            spread=float(market.get("spread", 0.0)),
            time_to_expiry_days=float(market.get("time_to_expiry_days", 999)),
            liquidity_score=score_market(market),
            confidence=float(market.get("confidence", 0.5)),
            metadata=market,
        )
        opportunities.append(opportunity)

    return sorted(opportunities, key=lambda x: x.liquidity_score, reverse=True)


def build_opportunity_list(opportunities: List[MarketOpportunity]) -> List[Dict[str, Any]]:
    """Produce a serializable ranked opportunity list."""
    return [opportunity.__dict__ for opportunity in opportunities]


if __name__ == "__main__":
    print("Prediction market scan module loaded.")
