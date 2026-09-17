"""Decides when a tracked pair's price/volume behavior is worth an alert --
both the entry and the exit side of a trade.

**Entry** ("volume_and_gain"): volume has picked up *and* price has run up
into the 10-15%+ zone while we've been watching it -- the classic
early-memecoin-pump shape.

**Exit** ("pullback"): a pair that already fired an entry alert has since
pulled back a meaningful chunk from its recent peak -- the moment this tool
can actually help with the other half of "quick in, quick out": it doesn't
predict a top, but it does tell you the run you got in on is now giving
some of itself back.

Each pair gets a simple armed/fired latch per alert type so it alerts once
per run-up (and once per pullback) instead of every poll while conditions
hold, and both reset together once price cools back off the entry
threshold, so a later, separate pump can alert on both again."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Alert, TrackedPair

DEFAULT_GAIN_MIN_PCT = 10.0
DEFAULT_GAIN_TARGET_MAX_PCT = 15.0
DEFAULT_VOLUME_MULTIPLIER = 2.0
DEFAULT_RESET_BUFFER_PCT = 5.0  # gain must drop this far below gain_min_pct before re-arming
DEFAULT_EXIT_DRAWDOWN_PCT = 8.0  # pullback-from-peak that triggers an exit alert


@dataclass
class SignalThresholds:
    gain_min_pct: float = DEFAULT_GAIN_MIN_PCT
    gain_target_max_pct: float = DEFAULT_GAIN_TARGET_MAX_PCT
    volume_multiplier: float = DEFAULT_VOLUME_MULTIPLIER
    reset_buffer_pct: float = DEFAULT_RESET_BUFFER_PCT
    exit_drawdown_pct: float = DEFAULT_EXIT_DRAWDOWN_PCT
    require_volume: bool = True  # False: alert on price gain alone, ignoring volume
    require_gain: bool = True  # False: alert on volume spike alone, ignoring price


@dataclass
class _PairState:
    fired: bool = False
    pullback_fired: bool = False


class SignalEngine:
    def __init__(self, thresholds: SignalThresholds | None = None) -> None:
        self.thresholds = thresholds or SignalThresholds()
        self._states: dict[str, _PairState] = {}

    def forget(self, pair_address: str) -> None:
        self._states.pop(pair_address, None)

    def evaluate(self, tracked: TrackedPair) -> list[Alert]:
        t = self.thresholds
        state = self._states.setdefault(tracked.pair.pair_address, _PairState())
        alerts: list[Alert] = []

        gain_ok = (not t.require_gain) or tracked.gain_pct >= t.gain_min_pct
        volume_ok = (not t.require_volume) or tracked.volume_multiplier >= t.volume_multiplier
        qualifies = gain_ok and volume_ok

        reset_level = t.gain_min_pct - t.reset_buffer_pct
        if state.fired and tracked.gain_pct <= reset_level:
            state.fired = False
            state.pullback_fired = False  # a fresh pump later can alert on both again

        if qualifies and not state.fired:
            state.fired = True
            alerts.append(_build_entry_alert(tracked, t))

        if state.fired and not state.pullback_fired and tracked.drawdown_from_peak_pct >= t.exit_drawdown_pct:
            state.pullback_fired = True
            alerts.append(_build_pullback_alert(tracked, t))

        return alerts


def _build_entry_alert(tracked: TrackedPair, t: SignalThresholds) -> Alert:
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


def _build_pullback_alert(tracked: TrackedPair, t: SignalThresholds) -> Alert:
    pair = tracked.pair
    message = (
        f"{pair.base_symbol} ({pair.dex_id}) pulled back {tracked.drawdown_from_peak_pct:.1f}% "
        f"from its recent peak of ${tracked.peak_price:.8f} -- now ${pair.price_usd:.8f} "
        f"(still {tracked.gain_pct:+.1f}% since first tracked). Consider your exit. {pair.url}"
    )
    return Alert(
        event="pullback",
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
