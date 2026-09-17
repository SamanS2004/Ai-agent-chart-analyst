from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

SignalEvent = Literal["price_gain", "volume_spike", "volume_and_gain", "new_pair"]


@dataclass(frozen=True)
class TokenPair:
    """One DexScreener trading pair for a memecoin on a supported chain
    (Solana or Robinhood Chain -- see discovery.py). A token can have
    several pairs across DEXes/pools; discovery picks the highest-liquidity
    one per token as canonical."""

    chain_id: str
    dex_id: str
    pair_address: str
    base_token_address: str
    base_symbol: str
    base_name: str
    price_usd: float
    liquidity_usd: float
    fdv: Optional[float]
    volume_m5: float
    volume_h1: float
    volume_h6: float
    volume_h24: float
    price_change_m5: float
    price_change_h1: float
    price_change_h6: float
    price_change_h24: float
    pair_created_at_ms: Optional[int]
    url: str


@dataclass(frozen=True)
class TrackedPair:
    """A TokenPair plus this agent's own read on it: gain/volume figures
    computed from our own polling history when we have enough of it,
    falling back to DexScreener's own windows on the first few polls."""

    pair: TokenPair
    gain_pct: float
    gain_basis: Literal["local", "api_h1"]
    volume_multiplier: float
    volume_basis: Literal["local", "api_h1_rate"]
    first_seen_ms: int
    last_updated_ms: int


@dataclass(frozen=True)
class Alert:
    event: SignalEvent
    pair_address: str
    symbol: str
    name: str
    dex_id: str
    price_usd: float
    gain_pct: float
    volume_multiplier: float
    volume_usd_recent: float
    liquidity_usd: float
    url: str
    timestamp_ms: int
    message: str
