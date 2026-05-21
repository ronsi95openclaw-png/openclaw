"""Crypto.com MCP data bridge — normalizes MCP tool output to internal candle format.

Architecture role:
  Claude Code sessions  → call mcp__f177133f__get_candlestick() / get_ticker()
                          → inject via CryptoComMCPBridge.inject_candles()
  Standalone bot        → falls back to trading/exchange.py REST API automatically

The internal candle format is:
    {"ts": int, "open": float, "high": float, "low": float,
     "close": float, "volume": float}

Usage (within a Claude Code session after calling MCP tools):
    from trading.cryptocom_mcp_bridge import CryptoComMCPBridge
    bridge = CryptoComMCPBridge()
    bridge.inject_mcp_candles("BTC_USDT", raw_mcp_response)
    candles = bridge.fetch_candles("BTC_USDT", "15m", 100)

Usage (standalone — automatic REST fallback):
    bridge = CryptoComMCPBridge()
    candles = bridge.fetch_candles("BTC_USDT", "15m", 100)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.trading.cryptocom_mcp")

# MCP instrument names → internal symbol map
_MCP_TO_INTERNAL = {
    "BTCUSD-PERP":  "BTC_USDT",
    "ETHUSD-PERP":  "ETH_USDT",
    "SOLUSD-PERP":  "SOL_USDT",
    "BTC_USDT":     "BTC_USDT",
    "ETH_USDT":     "ETH_USDT",
    "SOL_USDT":     "SOL_USDT",
    "BTC-USDT":     "BTC_USDT",
    "ETH-USDT":     "ETH_USDT",
    "SOL-USDT":     "SOL_USDT",
}

# Reverse map: internal → Crypto.com MCP instrument name (perpetuals for live trading)
_INTERNAL_TO_MCP = {
    "BTC_USDT": "BTCUSD-PERP",
    "ETH_USDT": "ETHUSD-PERP",
    "SOL_USDT": "SOLUSD-PERP",
}

# MCP timeframe labels
_TIMEFRAME_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1D",
}


class CryptoComMCPBridge:
    """Provides fetch_candles / fetch_ticker with MCP injection + REST fallback.

    Injected data (from Claude Code MCP tool calls) takes precedence over REST.
    Once injected, data is held for the lifetime of this instance.
    """

    def __init__(self) -> None:
        self._injected_candles: Dict[str, List[Dict]] = {}
        self._injected_tickers: Dict[str, Dict]       = {}

    # ── Injection (called from Claude Code sessions) ──────────────────────────

    def inject_mcp_candles(
        self,
        symbol: str,
        mcp_response: Any,
    ) -> int:
        """Parse raw MCP get_candlestick response and store normalized candles.

        The MCP tool returns a dict with data.result.data list of OHLCV dicts or
        a list of arrays. We normalize to internal format.

        Returns number of candles stored.
        """
        internal = _MCP_TO_INTERNAL.get(symbol, symbol)
        candles  = _normalize_mcp_candles(mcp_response)
        if candles:
            self._injected_candles[internal] = candles
            logger.info("MCP bridge: injected %d candles for %s", len(candles), internal)
        return len(candles)

    def inject_mcp_ticker(self, symbol: str, mcp_response: Any) -> None:
        """Parse raw MCP get_ticker response and store normalized ticker."""
        internal = _MCP_TO_INTERNAL.get(symbol, symbol)
        ticker   = _normalize_mcp_ticker(mcp_response)
        if ticker:
            self._injected_tickers[internal] = ticker
            logger.info("MCP bridge: injected ticker for %s: %.4f", internal, ticker.get("last", 0))

    def clear(self, symbol: Optional[str] = None) -> None:
        if symbol:
            internal = _MCP_TO_INTERNAL.get(symbol, symbol)
            self._injected_candles.pop(internal, None)
            self._injected_tickers.pop(internal, None)
        else:
            self._injected_candles.clear()
            self._injected_tickers.clear()

    # ── Data access (REST fallback) ───────────────────────────────────────────

    def fetch_candles(
        self,
        symbol: str,
        timeframe: str = "15m",
        count: int = 100,
    ) -> List[Dict]:
        """Return candles for symbol. Uses injected MCP data if available, else REST."""
        internal = _MCP_TO_INTERNAL.get(symbol, symbol)
        if internal in self._injected_candles:
            candles = self._injected_candles[internal]
            return candles[-count:] if len(candles) > count else candles
        return _rest_fetch_candles(internal, timeframe, count)

    def fetch_ticker(self, symbol: str) -> Dict:
        """Return ticker for symbol. Uses injected MCP data if available, else REST."""
        internal = _MCP_TO_INTERNAL.get(symbol, symbol)
        if internal in self._injected_tickers:
            return self._injected_tickers[internal]
        return _rest_fetch_ticker(internal)

    def fetch_live_context_summary(self, symbols: List[str] = None) -> str:
        """Return a text summary of all injected live data for analyst prompt context."""
        if symbols is None:
            symbols = list(self._injected_candles.keys()) or ["BTC_USDT", "ETH_USDT", "SOL_USDT"]

        lines = ["Crypto.com live market data (from MCP tools):"]
        for sym in symbols:
            internal = _MCP_TO_INTERNAL.get(sym, sym)
            if internal in self._injected_tickers:
                t = self._injected_tickers[internal]
                lines.append(
                    f"  {internal}: last={t.get('last', '?'):.4f}  "
                    f"bid={t.get('bid', '?'):.4f}  ask={t.get('ask', '?'):.4f}  "
                    f"24h_vol={t.get('volume_24h', 0):.2f}"
                )
            if internal in self._injected_candles:
                c = self._injected_candles[internal]
                if c:
                    last = c[-1]
                    chg  = (last["close"] - c[0]["open"]) / c[0]["open"] * 100
                    lines.append(
                        f"  {internal}: {len(c)} candles  "
                        f"open={c[0]['open']:.4f}  close={last['close']:.4f}  "
                        f"range_chg={chg:+.2f}%"
                    )
        return "\n".join(lines)

    @staticmethod
    def mcp_instrument(internal_symbol: str) -> str:
        """Return the Crypto.com MCP instrument name for an internal symbol."""
        return _INTERNAL_TO_MCP.get(internal_symbol, internal_symbol)


# ── Normalization helpers ─────────────────────────────────────────────────────

def _normalize_mcp_candles(response: Any) -> List[Dict]:
    """Extract candle list from various MCP response shapes.

    Handles:
      {"data": [...]}                    ← Crypto.com MCP get_candlestick
      {"data": {"result": {"data": []}}} ← nested result
      [...]                              ← bare list
    """
    raw: Any = None

    if isinstance(response, list):
        raw = response
    elif isinstance(response, dict):
        inner = response.get("data")
        if isinstance(inner, list):
            # {"data": [...]} — direct candle list (Crypto.com MCP format)
            raw = inner
        elif isinstance(inner, dict):
            # {"data": {"result": {"data": [...]}}}
            result = inner.get("result", inner)
            if isinstance(result, dict):
                raw = result.get("data", result.get("candles", result.get("result")))
            elif isinstance(result, list):
                raw = result
        if raw is None:
            raw = response.get("result", response.get("candles"))

    if not isinstance(raw, list) or not raw:
        logger.warning("MCP candle normalize: unexpected response shape: %s", type(response))
        return []

    candles = []
    for item in raw:
        try:
            if isinstance(item, dict):
                # Crypto.com MCP uses ISO timestamp strings; fallback to epoch int
                ts_raw = item.get("timestamp", item.get("t", item.get("ts", 0)))
                if isinstance(ts_raw, str):
                    from datetime import datetime, timezone
                    t = int(datetime.fromisoformat(
                        ts_raw.replace("Z", "+00:00")
                    ).timestamp())
                else:
                    t = int(ts_raw)
                o = float(item.get("open",   item.get("o", 0)))
                h = float(item.get("high",   item.get("h", 0)))
                l = float(item.get("low",    item.get("l", 0)))
                c = float(item.get("close",  item.get("c", 0)))
                v = float(item.get("volume", item.get("v", 0)))
            elif isinstance(item, (list, tuple)) and len(item) >= 6:
                # Array format: [ts, open, high, low, close, volume]
                t, o, h, l, c, v = (
                    int(item[0]), float(item[1]), float(item[2]),
                    float(item[3]), float(item[4]), float(item[5]),
                )
            else:
                continue
            if o > 0 and c > 0:
                candles.append({"ts": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
        except Exception:
            continue

    candles.sort(key=lambda x: x["ts"])
    return candles


def _normalize_mcp_ticker(response: Any) -> Optional[Dict]:
    """Extract ticker from MCP get_ticker response."""
    raw: Any = response
    if isinstance(raw, dict):
        for key in ("data", "result", "ticker"):
            if key in raw and isinstance(raw[key], dict):
                raw = raw[key]
                break
    if not isinstance(raw, dict):
        return None
    try:
        return {
            "last":       float(raw.get("a", raw.get("last",  raw.get("lastTradePrice", 0)))),
            "bid":        float(raw.get("b", raw.get("bid",   0))),
            "ask":        float(raw.get("k", raw.get("ask",   0))),
            "volume_24h": float(raw.get("v", raw.get("volume_24h", 0))),
            "high_24h":   float(raw.get("h", raw.get("high_24h",   0))),
            "low_24h":    float(raw.get("l", raw.get("low_24h",    0))),
        }
    except Exception:
        return None


# ── REST fallbacks ────────────────────────────────────────────────────────────

def _rest_fetch_candles(symbol: str, timeframe: str, count: int) -> List[Dict]:
    try:
        from trading.exchange import fetch_candles
        return fetch_candles(symbol, timeframe, count)
    except Exception as exc:
        logger.warning("REST candles fallback failed [%s]: %s", symbol, exc)
        return []


def _rest_fetch_ticker(symbol: str) -> Dict:
    try:
        from trading.exchange import fetch_ticker
        return fetch_ticker(symbol)
    except Exception as exc:
        logger.warning("REST ticker fallback failed [%s]: %s", symbol, exc)
        return {}


# ── Singleton ─────────────────────────────────────────────────────────────────

_bridge: Optional[CryptoComMCPBridge] = None


def get_bridge() -> CryptoComMCPBridge:
    global _bridge
    if _bridge is None:
        _bridge = CryptoComMCPBridge()
    return _bridge
