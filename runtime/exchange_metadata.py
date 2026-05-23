"""Authoritative instrument metadata registry for OpenClaw.

Provides per-instrument specs (precision, lot sizes, leverage limits) for the
three perpetual futures the bot trades.  Hardcoded defaults are always the
safety fallback — the module never raises when live data is unavailable.

Instruments
-----------
    BTC_USDT  →  BTCUSD-PERP
    ETH_USDT  →  ETHUSD-PERP
    SOL_USDT  →  SOLUSD-PERP

Thread-safety
-------------
All mutable state is protected by a single threading.Lock.  Reads and writes
both acquire the lock, so callers need not synchronise externally.

Quantity normalisation
----------------------
``normalize_quantity`` TRUNCATES (floor), never rounds.

    qty_precision=3: 0.00167 → 0.001   (NOT 0.002)
    qty_precision=0: 1.7     → 1       (NOT 2)

This is intentional — the exchange rejects quantities larger than the
authorised amount, so we always err on the side of the smaller value.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.exchange_metadata")

# ── Canonical instrument name aliases ────────────────────────────────────────

#: Maps internal ticker → canonical exchange instrument name
_TICKER_TO_INSTRUMENT: Dict[str, str] = {
    "BTC_USDT": "BTCUSD-PERP",
    "ETH_USDT": "ETHUSD-PERP",
    "SOL_USDT": "SOLUSD-PERP",
    # Accept canonical names directly too
    "BTCUSD-PERP": "BTCUSD-PERP",
    "ETHUSD-PERP": "ETHUSD-PERP",
    "SOLUSD-PERP": "SOLUSD-PERP",
}

# ── Hardcoded defaults (always the safety fallback) ──────────────────────────

_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "BTCUSD-PERP": {
        "instrument_name": "BTCUSD-PERP",
        "qty_precision": 3,
        "price_precision": 1,
        "min_qty": 0.001,
        "max_leverage": 100,
        "tick_size": 0.5,
        "lot_size": 0.001,
        "min_notional": 10.0,
        "supported_order_types": ["LIMIT", "MARKET", "STOP_LOSS", "STOP_LIMIT"],
        "leverage_brackets": [],
        "last_refreshed_ts": 0.0,
    },
    "ETHUSD-PERP": {
        "instrument_name": "ETHUSD-PERP",
        "qty_precision": 2,
        "price_precision": 2,
        "min_qty": 0.01,
        "max_leverage": 100,
        "tick_size": 0.05,
        "lot_size": 0.01,
        "min_notional": 10.0,
        "supported_order_types": ["LIMIT", "MARKET", "STOP_LOSS", "STOP_LIMIT"],
        "leverage_brackets": [],
        "last_refreshed_ts": 0.0,
    },
    "SOLUSD-PERP": {
        "instrument_name": "SOLUSD-PERP",
        "qty_precision": 0,
        "price_precision": 3,
        "min_qty": 1.0,
        "max_leverage": 20,
        "tick_size": 0.001,
        "lot_size": 1.0,
        "min_notional": 10.0,
        "supported_order_types": ["LIMIT", "MARKET", "STOP_LOSS", "STOP_LIMIT"],
        "leverage_brackets": [],
        "last_refreshed_ts": 0.0,
    },
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSpec:
    instrument_name: str
    qty_precision: int
    price_precision: int
    min_qty: float
    max_leverage: int
    tick_size: float
    lot_size: float
    min_notional: float
    supported_order_types: List[str] = field(default_factory=list)
    leverage_brackets: List[dict] = field(default_factory=list)
    last_refreshed_ts: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "InstrumentSpec":
        return cls(
            instrument_name=d["instrument_name"],
            qty_precision=int(d["qty_precision"]),
            price_precision=int(d["price_precision"]),
            min_qty=float(d["min_qty"]),
            max_leverage=int(d["max_leverage"]),
            tick_size=float(d["tick_size"]),
            lot_size=float(d["lot_size"]),
            min_notional=float(d["min_notional"]),
            supported_order_types=list(d.get("supported_order_types", [])),
            leverage_brackets=list(d.get("leverage_brackets", [])),
            last_refreshed_ts=float(d.get("last_refreshed_ts", 0.0)),
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class ExchangeMetadataRegistry:
    """Thread-safe instrument metadata registry with live-refresh and fallback.

    Usage
    -----
        registry = ExchangeMetadataRegistry()
        spec = registry.get_spec("BTC_USDT")
        qty  = registry.normalize_quantity("BTC_USDT", raw_qty)
    """

    def __init__(
        self,
        refresh_interval_hours: float = 6.0,
        fallback_path: str = "data/exchange_metadata.json",
    ) -> None:
        self._refresh_interval_hours = refresh_interval_hours
        self._fallback_path = fallback_path
        self._lock = threading.Lock()

        # Initialise from hardcoded defaults, then overlay persisted data
        self._specs: Dict[str, InstrumentSpec] = {
            name: InstrumentSpec.from_dict(d) for name, d in _DEFAULTS.items()
        }
        self._load_fallback()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_spec(self, instrument: str) -> InstrumentSpec:
        """Return the InstrumentSpec for *instrument*.

        Accepts either ticker form (``BTC_USDT``) or canonical exchange name
        (``BTCUSD-PERP``).  Triggers a background-eligible refresh if the data
        is stale, but always returns immediately with cached data.

        Raises
        ------
        KeyError
            If the instrument is not known.
        """
        canonical = self._resolve(instrument)
        self._maybe_refresh(canonical)
        with self._lock:
            return self._specs[canonical]

    def normalize_quantity(self, instrument: str, qty: float) -> float:
        """Truncate *qty* to the instrument's qty_precision (floor, not round).

        Examples (BTCUSD-PERP, precision=3)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            0.00167 → 0.001
            0.00999 → 0.009

        Examples (SOLUSD-PERP, precision=0)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            1.7 → 1
            2.9 → 2

        Raises
        ------
        ValueError
            If the truncated result is below min_qty.
        """
        spec = self.get_spec(instrument)
        precision = spec.qty_precision
        factor = 10 ** precision
        truncated = math.floor(qty * factor) / factor
        if truncated < spec.min_qty:
            raise ValueError(
                f"normalize_quantity({instrument}): {qty} truncates to {truncated} "
                f"which is below min_qty={spec.min_qty}"
            )
        return truncated

    def normalize_price(self, instrument: str, price: float) -> float:
        """Round *price* to the instrument's price_precision."""
        spec = self.get_spec(instrument)
        return round(price, spec.price_precision)

    def validate_order(
        self,
        instrument: str,
        qty: float,
        price: Optional[float] = None,
        leverage: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Validate an order against instrument constraints.

        Returns
        -------
        (True, "")
            Order passes all checks.
        (False, reason)
            Order fails; *reason* describes the first failing check.
        """
        try:
            spec = self.get_spec(instrument)
        except KeyError:
            return False, f"Unknown instrument: {instrument}"

        if qty < spec.min_qty:
            return False, (
                f"qty {qty} is below min_qty {spec.min_qty} for {instrument}"
            )

        if leverage is not None and leverage > spec.max_leverage:
            return False, (
                f"leverage {leverage}x exceeds max_leverage {spec.max_leverage}x "
                f"for {instrument}"
            )

        if price is not None and price > 0:
            notional = qty * price
            if notional < spec.min_notional:
                return False, (
                    f"notional {notional:.4f} (qty={qty} * price={price}) is below "
                    f"min_notional {spec.min_notional} for {instrument}"
                )

        return True, ""

    def refresh(self, instrument: Optional[str] = None) -> None:
        """Attempt to fetch live specs from the exchange.

        Fetches all instruments if *instrument* is None, or just the requested
        one otherwise.  Falls back to hardcoded defaults on any failure.
        Never raises.
        """
        targets = (
            [self._resolve(instrument)]
            if instrument is not None
            else list(_DEFAULTS.keys())
        )
        try:
            self._fetch_live(targets)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "exchange_metadata: live refresh failed (%s), using hardcoded defaults",
                exc,
            )
            self._apply_defaults(targets)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve(self, instrument: str) -> str:
        """Normalise *instrument* to canonical exchange name or raise KeyError."""
        canonical = _TICKER_TO_INSTRUMENT.get(instrument)
        if canonical is None:
            raise KeyError(
                f"Unknown instrument '{instrument}'. "
                f"Known: {list(_TICKER_TO_INSTRUMENT.keys())}"
            )
        return canonical

    def _maybe_refresh(self, canonical: str) -> None:
        """Refresh *canonical* if its data is older than refresh_interval_hours."""
        with self._lock:
            spec = self._specs.get(canonical)
            last_ts = spec.last_refreshed_ts if spec else 0.0

        age_hours = (time.time() - last_ts) / 3600.0
        if age_hours >= self._refresh_interval_hours:
            logger.debug(
                "exchange_metadata: %s is %.1f h old (threshold %.1f h), refreshing",
                canonical, age_hours, self._refresh_interval_hours,
            )
            self.refresh(canonical)

    def _fetch_live(self, targets: List[str]) -> None:
        """Fetch instrument specs from the exchange REST API.

        Parses qty_step, price_step, and min_quantity from the response and
        overlays the matching InstrumentSpec fields.  Unrecognised fields are
        ignored.  Raises on any network or parse error so the caller can fall
        back gracefully.
        """
        import requests  # local import — not required at module load
        try:
            from trading.exchange import _PUBLIC as PUBLIC_BASE  # type: ignore
        except ImportError:
            PUBLIC_BASE = "https://api.crypto.com/exchange/v1/public"

        url = f"{PUBLIC_BASE}/get-instruments"
        resp = requests.get(url, params={"type": "PERPETUAL_SWAP"}, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        instruments_raw: List[dict] = (
            data.get("result", {}).get("data", [])
            or data.get("result", [])
            or []
        )

        now = time.time()
        updated: Dict[str, InstrumentSpec] = {}

        for item in instruments_raw:
            name = item.get("instrument_name", "")
            if name not in targets:
                continue

            try:
                default = _DEFAULTS[name].copy()
                # qty_step → lot_size and qty_precision
                qty_step = float(item.get("qty_tick_size", item.get("qty_step", default["lot_size"])))
                price_step = float(item.get("price_tick_size", item.get("price_step", default["tick_size"])))
                min_qty = float(item.get("min_quantity", item.get("min_qty", default["min_qty"])))
                max_lev = int(item.get("max_leverage", default["max_leverage"]))

                # Derive precision from step size
                qty_precision = _step_to_precision(qty_step)
                price_precision = _step_to_precision(price_step)

                updated[name] = InstrumentSpec(
                    instrument_name=name,
                    qty_precision=qty_precision,
                    price_precision=price_precision,
                    min_qty=min_qty,
                    max_leverage=max_lev,
                    tick_size=price_step,
                    lot_size=qty_step,
                    min_notional=float(item.get("min_notional_value", default["min_notional"])),
                    supported_order_types=item.get("order_types", default["supported_order_types"]),
                    leverage_brackets=item.get("leverage_brackets", []),
                    last_refreshed_ts=now,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("exchange_metadata: failed to parse %s: %s", name, exc)

        # For any target not found in response, apply defaults with fresh ts
        for name in targets:
            if name not in updated:
                logger.warning(
                    "exchange_metadata: %s not found in live response, using defaults",
                    name,
                )
                spec = InstrumentSpec.from_dict({**_DEFAULTS[name], "last_refreshed_ts": now})
                updated[name] = spec

        with self._lock:
            self._specs.update(updated)

        self._persist()
        logger.info(
            "exchange_metadata: live refresh complete for %s", list(updated.keys())
        )

    def _apply_defaults(self, targets: List[str]) -> None:
        """Overlay hardcoded defaults for *targets*, updating last_refreshed_ts."""
        now = time.time()
        with self._lock:
            for name in targets:
                if name in _DEFAULTS:
                    self._specs[name] = InstrumentSpec.from_dict(
                        {**_DEFAULTS[name], "last_refreshed_ts": now}
                    )

    def _persist(self) -> None:
        """Atomically write current specs to fallback_path (tmp + os.replace)."""
        try:
            path = self._fallback_path
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with self._lock:
                payload = {
                    name: asdict(spec) for name, spec in self._specs.items()
                }
            serialised = json.dumps(payload, indent=2, sort_keys=True)
            # Atomic write via tmp file in the same directory
            dir_name = os.path.dirname(os.path.abspath(path))
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(serialised)
                os.replace(tmp_path, path)
            except Exception:
                # Clean up tmp on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.debug("exchange_metadata: persisted to %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("exchange_metadata: _persist failed: %s", exc)

    def _load_fallback(self) -> None:
        """Load specs from fallback_path; fill any gaps with hardcoded defaults."""
        path = self._fallback_path
        if not os.path.exists(path):
            logger.debug("exchange_metadata: no fallback file at %s, using defaults", path)
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload: Dict[str, Any] = json.load(fh)
            loaded: Dict[str, InstrumentSpec] = {}
            for name, raw in payload.items():
                if name in _DEFAULTS:
                    # Merge: persisted values take priority, defaults fill gaps
                    merged = {**_DEFAULTS[name], **raw, "instrument_name": name}
                    loaded[name] = InstrumentSpec.from_dict(merged)

            with self._lock:
                self._specs.update(loaded)

            logger.info(
                "exchange_metadata: loaded %d specs from %s", len(loaded), path
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "exchange_metadata: _load_fallback failed (%s), using hardcoded defaults",
                exc,
            )
            # Ensure defaults are in place
            with self._lock:
                for name, d in _DEFAULTS.items():
                    if name not in self._specs:
                        self._specs[name] = InstrumentSpec.from_dict(d)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _step_to_precision(step: float) -> int:
    """Derive decimal precision from a step value.

    Examples
    --------
        0.001 → 3
        0.01  → 2
        0.5   → 1
        1.0   → 0
    """
    if step >= 1.0:
        return 0
    # Use string representation for robustness against floating-point repr
    s = f"{step:.10f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".")[1])


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: Optional[ExchangeMetadataRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ExchangeMetadataRegistry:
    """Return the module-level singleton, initialising it on first call."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ExchangeMetadataRegistry()
    return _registry
