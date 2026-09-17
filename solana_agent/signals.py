"""Decides when a tracked pair's price/volume behavior is worth an alert.

The headline signal is "volume_and_gain": volume has picked up *and* price
has run up into the 10-15%+ zone while we've been watching it -- the classic
early-memecoin-pump shape. Each pair gets a simple armed/fired latch so it
alerts once per run-up instead of every poll while it stays elevated, and
resets once price cools back off so a later, separate pump can alert again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Alert, TrackedPair

DEFAULT_GAIN_MIN_PCT = 10.0
DEFAULT_GAIN_TARGET_MAX_PCT = 15.0
DEFAULT_VOLUME_MULTIPLIER = 2.0
DEFAULT_RESET_BUFFER_PCT = 5.0  # gain must drop this far below gain_min_pct before re-arming


@dataclass
class SignalThresholds:
    gain_min_pct: float = DEFAULT_GAIN_MIN_PCT
    gain_target_max_pct: float = DEFAULT_GAIN_TARGET_MAX_PCT
    volume_multiplier: float = DEFAULT_VOLUME_MULTIPLIER
    reset_buffer_pct: float = DEFAULT_RESET_BUFFER_PCT
    require_volume: bool = True  # False: alert on price gain alone, ignoring volume
    require_gain: bool = True  # False: alert on volume spike alone, ignoring price


@dataclass
class _PairState:
    fired: bool = False


class SignalEngine:
    def __init__(self, thresholds: SignalThresholds | None = None) -> None:
        self.thresholds = thresholds or SignalThresholds()
        self._states: dict[str, _PairState] = {}

    def forget(self, pair_address: str) -> None:
        self._states.pop(pair_address, None)

    def evaluate(self, tracked: TrackedPair) -> list[Alert]:
        t = self.thresholds
        state = self._states.setdefault(tracked.pair.pair_address, _PairState())

        gain_ok = (not t.require_gain) or tracked.gain_pct >= t.gain_min_pct
        volume_ok = (not t.require_volume) or tracked.volume_multiplier >= t.volume_multiplier
        qualifies = gain_ok and volume_ok

        reset_level = t.gain_min_pct - t.reset_buffer_pct
        if state.fired and tracked.gain_pct <= reset_level:
            state.fired = False

        if not qualifies or state.fired:
            return []

        state.fired = True
        return [_build_alert(tracked, t)]


def _build_alert(tracked: TrackedPair, t: SignalThresholds) -> Alert:
    pair = tracked.pair
    in_target_zone = t.gain_min_pct <= tracked.gain_pct <= t.gain_target_max_pct
    zone_note = (
        f"within the {t.gain_min_pct:.0f}-{t.gain_target_max_pct:.0f}% target zone"
        if in_target_zone
        else f"past the {t.gain_target_max_pct:.0f}% target zone"
    )
    message = (
        f"{pair.base_symbol} ({pair.dex_id}) up {tracked.gain_pct:.1f}% "
        f"({zone_note}) on {tracked.volume_multiplier:.1f}x volume -- "
        f"${pair.price_usd:.8f}, 5m vol ${pair.volume_m5:,.0f}, "
        f"liquidity ${pair.liquidity_usd:,.0f}. {pair.url}"
    )
    return Alert(
        event="volume_and_gain",
        pair_address=pair.pair_address,
        symbol=pair.base_symbol,
        name=pair.base_name,
        dex_id=pair.dex_id,
        price_usd=pair.price_usd,
        gain_pct=tracked.gain_pct,
        volume_multiplier=tracked.volume_multiplier,
        volume_usd_recent=pair.volume_m5,
        liquidity_usd=pair.liquidity_usd,
        url=pair.url,
        timestamp_ms=tracked.last_updated_ms,
        message=message,
    )
