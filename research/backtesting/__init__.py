"""Backtesting sub-package for the OpenClaw research engine.

Exports the primary classes used by callers:
    BacktestEngine        – event-driven simulation engine
    ReportGenerator       – HTML / CSV / JSON / Markdown report writer
    ExecutionModel        – unified fill + slippage + fee pipeline
    FillSimulator         – order fill simulation
    BloFinFeeModel        – BloFin exchange fee schedule
    FixedBpsSlippage      – simple slippage model
    VolumeImpactSlippage  – square-root market-impact model
    FixedFundingModel     – constant funding rate
    MarketReplayer        – historical replay with latency simulation
"""
from __future__ import annotations

from research.backtesting.engine import BacktestEngine, OpenPosition, PortfolioState
from research.backtesting.execution_model import ExecutionModel
from research.backtesting.fees import BloFinFeeModel, FeeModel, ZeroFeeModel
from research.backtesting.fills import FillResult, FillSimulator
from research.backtesting.funding import FixedFundingModel, FundingModel, HistoricalFundingModel
from research.backtesting.replay_market import MarketReplayer
from research.backtesting.reports import ReportGenerator
from research.backtesting.slippage import (
    FixedBpsSlippage,
    SlippageModel,
    VolumeImpactSlippage,
    ZeroSlippage,
)

__all__ = [
    "BacktestEngine",
    "OpenPosition",
    "PortfolioState",
    "ExecutionModel",
    "FeeModel",
    "BloFinFeeModel",
    "ZeroFeeModel",
    "FillResult",
    "FillSimulator",
    "FundingModel",
    "FixedFundingModel",
    "HistoricalFundingModel",
    "MarketReplayer",
    "ReportGenerator",
    "SlippageModel",
    "ZeroSlippage",
    "FixedBpsSlippage",
    "VolumeImpactSlippage",
]
