"""OpenClaw global settings — all tunable parameters in one place.

Reads from environment variables with sensible defaults.
Import: from settings import DEMO_SLIPPAGE_PCT, DCA_CONFIG, ...
"""
from __future__ import annotations
import os

# ── Demo realism ──────────────────────────────────────────────────────────────
DEMO_SLIPPAGE_PCT        = float(os.getenv("DEMO_SLIPPAGE_PCT",  "0.0015"))  # 0.15% one-way
DEMO_SPREAD_PCT          = float(os.getenv("DEMO_SPREAD_PCT",    "0.0005"))  # 0.05% spread
DEMO_PARTIAL_FILL_ENABLED = os.getenv("DEMO_PARTIAL_FILL", "true").lower() == "true"
DEMO_FILL_DELAY_MS       = int(os.getenv("DEMO_FILL_DELAY_MS",   "200"))

# ── DCA configuration ─────────────────────────────────────────────────────────
DCA_CONFIG = {
    "enabled":            os.getenv("DCA_ENABLED", "true").lower() == "true",
    "schedule_hours":     int(os.getenv("DCA_SCHEDULE_HOURS",     "4")),
    "dip_trigger_pct":    float(os.getenv("DCA_DIP_TRIGGER_PCT",  "2.0")),
    "max_single_buy_pct": float(os.getenv("DCA_MAX_SINGLE_BUY_PCT", "0.05")),
    "allocations": {
        "BTC_USDT": float(os.getenv("DCA_ALLOC_BTC", "0.40")),
        "ETH_USDT": float(os.getenv("DCA_ALLOC_ETH", "0.30")),
        "SOL_USDT": float(os.getenv("DCA_ALLOC_SOL", "0.20")),
    },
}

# ── Live mode gate ────────────────────────────────────────────────────────────
LIVE_ACTIVATION_PASSPHRASE = os.getenv(
    "LIVE_ACTIVATION_PASSPHRASE", "CLAWBOT-GO-LIVE-CONFIRMED"
)
