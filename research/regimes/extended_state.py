"""Extended regime state with funding, liquidation, and news spike signals."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from research.types import RegimeState


@dataclass
class ExtendedRegimeState(RegimeState):
    """Extends base RegimeState with funding, liquidation, news signals."""

    funding_rate_8h: float = 0.0
    funding_extreme: bool = False
    liquidation_cascade: bool = False
    news_spike: bool = False
    confidence: float = 0.5
    rolling_regime_labels: Optional[List[str]] = None
    regime_stability: float = 1.0

    def __post_init__(self) -> None:
        if self.rolling_regime_labels is None:
            self.rolling_regime_labels = []
