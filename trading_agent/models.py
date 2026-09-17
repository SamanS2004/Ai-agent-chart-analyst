from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Kind = Literal["bullish", "bearish"]

# How price behaved the first time it came back to a zone: "retest" means it
# wicked in and closed back out (respected the level); "disrespect" means a
# candle closed all the way through it. See smc.py's fvg_is_tradeable /
# order_block_is_tradeable for what each kind of zone does with this.
TouchType = Literal["retest", "disrespect"]


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)


@dataclass
class FairValueGap:
    kind: Kind
    top: float
    bottom: float
    index: int
    timestamp_ms: int
    mitigated: bool = False
    mitigation_type: Optional[TouchType] = None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class OrderBlock:
    kind: Kind
    top: float
    bottom: float
    index: int
    timestamp_ms: int
    mitigated: bool = False
    mitigation_type: Optional[TouchType] = None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class ConfluenceZone:
    kind: Kind
    top: float
    bottom: float
    fvg: FairValueGap
    order_block: OrderBlock


@dataclass
class PriorityZone:
    """The single zone selected to represent a stack of same-direction FVGs
    that formed back to back (one continuous imbalance, not separate
    opportunities) -- see stacking.py."""

    kind: Kind
    top: float
    bottom: float
    stack_size: int
    has_confluence: bool
    is_widest_in_stack: bool
    order_block: Optional[OrderBlock] = None


@dataclass
class TradeIdea:
    kind: Literal["long", "short"]
    entry: float
    stop: float
    targets: list[float]
    confidence: Literal["low", "medium", "high"]
    zone_type: str
    rationale: str
    timestamp_ms: int

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


@dataclass
class AnalysisResult:
    symbol: str
    interval: int
    timestamp_ms: int
    last_price: float
    fair_value_gaps: list[FairValueGap] = field(default_factory=list)
    order_blocks: list[OrderBlock] = field(default_factory=list)
    confluence_zones: list[ConfluenceZone] = field(default_factory=list)
    trade_idea: Optional[TradeIdea] = None
