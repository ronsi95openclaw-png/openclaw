"""SimulationMode enum and SimulationConfig dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SimulationMode(Enum):
    ZERO = "zero"               # no friction
    REALISTIC = "realistic"     # normal market conditions
    STRESS = "stress"           # 3× all costs
    DETERMINISTIC = "deterministic"  # seeded, reproducible


@dataclass
class SimulationConfig:
    mode: SimulationMode = SimulationMode.REALISTIC
    slippage_model: object = field(default=None)
    liquidity_model: object = field(default=None)
    latency_injector: object = field(default=None)
    partial_fill_simulator: object = field(default=None)
    fee_model: object = field(default=None)
    market_impact_model: object = field(default=None)
    avg_daily_volume_usd: float = 500_000_000.0   # BTC default
    bar_duration_ms: int = 900_000                # 15 min
    seed: int = 42

    def __post_init__(self) -> None:
        # Import here to avoid circular imports; lazily populate defaults
        from research.simulation.slippage import SlippageModel
        from research.simulation.liquidity import LiquidityModel
        from research.simulation.latency import LatencyInjector
        from research.simulation.partial_fills import PartialFillSimulator
        from research.simulation.fees import FeeModel
        from research.simulation.market_impact import MarketImpactModel

        if self.slippage_model is None:
            self.slippage_model = SlippageModel(mode=self.mode)
        if self.liquidity_model is None:
            self.liquidity_model = LiquidityModel()
        if self.latency_injector is None:
            self.latency_injector = LatencyInjector(seed=self.seed)
        if self.partial_fill_simulator is None:
            self.partial_fill_simulator = PartialFillSimulator()
        if self.fee_model is None:
            self.fee_model = FeeModel()
        if self.market_impact_model is None:
            self.market_impact_model = MarketImpactModel()
