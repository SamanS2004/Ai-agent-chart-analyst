"""Turns a stream of TokenPair snapshots (one per poll) into a per-pair read
on "how far has this run up since we started watching it, right now" and
"is volume accelerating right now" -- the two ingredients signals.py combines
into a volume+gain alert.

DexScreener's own priceChange.h1/volume.h1 fields are fixed rolling windows
that don't know when *we* started watching a coin, so as soon as we have
enough of our own polling history for a pair, local figures (computed from
our own snapshots) replace them; until then the API's h1 window is used as
a reasonable first-poll estimate so a freshly-discovered pair isn't invisible
to signals for its first several minutes.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

from .models import TokenPair, TrackedPair

DEFAULT_LOOKBACK_SECONDS = 3600
MIN_LOCAL_SPAN_SECONDS = 600  # need >=10 min of our own history before trusting it over the API window
MIN_SAMPLES_FOR_LOCAL_VOLUME = 3
VOLUME_MULTIPLIER_CAP = 1000.0


@dataclass
class _Snapshot:
    timestamp_ms: int
    price_usd: float
    volume_m5: float


class PairTracker:
    """Not thread-safe by itself -- callers running discovery/polling and
    alert dispatch concurrently should serialize update() calls (agent.py
    runs everything on a single poll loop, so this is fine as-is)."""

    def __init__(
        self,
        lookback_seconds: int = DEFAULT_LOOKBACK_SECONDS,
        min_local_span_seconds: int = MIN_LOCAL_SPAN_SECONDS,
        min_samples_for_local_volume: int = MIN_SAMPLES_FOR_LOCAL_VOLUME,
    ) -> None:
        self.lookback_seconds = lookback_seconds
        self.min_local_span_seconds = min_local_span_seconds
        self.min_samples_for_local_volume = min_samples_for_local_volume
        self._history: dict[str, deque[_Snapshot]] = {}
        self._first_seen_ms: dict[str, int] = {}

    def forget(self, pair_address: str) -> None:
        self._history.pop(pair_address, None)
        self._first_seen_ms.pop(pair_address, None)

    def known_pair_addresses(self) -> list[str]:
        return list(self._history.keys())

    def update(self, pair: TokenPair, now_ms: int) -> TrackedPair:
        history = self._history.setdefault(pair.pair_address, deque())
        history.append(_Snapshot(now_ms, pair.price_usd, pair.volume_m5))

        cutoff_ms = now_ms - self.lookback_seconds * 1000
        while len(history) > 1 and history[0].timestamp_ms < cutoff_ms:
            history.popleft()

        first_seen_ms = self._first_seen_ms.setdefault(pair.pair_address, now_ms)

        gain_pct, gain_basis = self._compute_gain(pair, history)
        volume_multiplier, volume_basis = self._compute_volume_multiplier(pair, history)

        return TrackedPair(
            pair=pair,
            gain_pct=gain_pct,
            gain_basis=gain_basis,
            volume_multiplier=volume_multiplier,
            volume_basis=volume_basis,
            first_seen_ms=first_seen_ms,
            last_updated_ms=now_ms,
        )

    def _compute_gain(self, pair: TokenPair, history: deque[_Snapshot]):
        span_seconds = (history[-1].timestamp_ms - history[0].timestamp_ms) / 1000
        if len(history) >= 2 and span_seconds >= self.min_local_span_seconds:
            recent_low = min(s.price_usd for s in history)
            if recent_low > 0:
                return (pair.price_usd - recent_low) / recent_low * 100.0, "local"
        return pair.price_change_h1, "api_h1"

    def _compute_volume_multiplier(self, pair: TokenPair, history: deque[_Snapshot]):
        if len(history) >= self.min_samples_for_local_volume:
            past = [s.volume_m5 for s in list(history)[:-1]]
            baseline = statistics.mean(past) if past else 0.0
            if baseline > 0:
                return min(pair.volume_m5 / baseline, VOLUME_MULTIPLIER_CAP), "local"
        baseline_rate = pair.volume_h1 / 12.0
        if baseline_rate > 0:
            return min(pair.volume_m5 / baseline_rate, VOLUME_MULTIPLIER_CAP), "api_h1_rate"
        return (1.0 if pair.volume_m5 == 0 else VOLUME_MULTIPLIER_CAP), "api_h1_rate"
