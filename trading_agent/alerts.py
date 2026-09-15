"""Live touch / retest / disrespect detection for FVG and order-block zones.

Definitions (bullish zone = support the agent expects to hold; bearish zone
= resistance):

- touch:      price first trades into the zone [bottom, top].
- retest:     a later touch of the same zone, after price had previously
              touched it and then moved back away (zone held in between).
- disrespect: price breaks through the *far* edge of the zone (below
              `bottom` for a bullish zone, above `top` for a bearish one)
              by more than a small buffer -- the zone failed to hold and
              is retired (no further alerts fire for it).

This module is pure logic: feed it prices (from a live stream, ticker
polls, or even candle closes) and zone definitions, and it emits Alert
events on state transitions. It has no network code of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import FairValueGap, OrderBlock

Event = Literal["touch", "retest", "disrespect"]

DEFAULT_DISRESPECT_BUFFER_PCT = 0.0005  # 0.05% beyond the far edge


@dataclass(frozen=True)
class ZoneSpec:
    key: tuple
    kind: Literal["bullish", "bearish"]
    top: float
    bottom: float
    label: str
    already_touched: bool = False


@dataclass
class _ZoneState:
    spec: ZoneSpec
    inside: bool = False
    touch_count: int = 0
    disrespected: bool = False


@dataclass
class Alert:
    event: Event
    kind: Literal["bullish", "bearish"]
    label: str
    top: float
    bottom: float
    price: float
    timestamp_ms: int | None
    message: str


def filter_zones_near_price(
    specs: list[ZoneSpec], price: float, proximity_pct: float
) -> list[ZoneSpec]:
    """Keep only zones within `proximity_pct` of price (or straddling it).
    A 200-candle history can carry dozens of old, far-away zones; without
    this, tracking (and alerting on) all of them buries the signal."""
    if proximity_pct <= 0:
        return specs
    max_distance = price * proximity_pct
    kept = []
    for spec in specs:
        if spec.bottom <= price <= spec.top:
            kept.append(spec)
            continue
        distance = spec.bottom - price if price < spec.bottom else price - spec.top
        if distance <= max_distance:
            kept.append(spec)
    return kept


def zone_specs_from_detections(
    fvgs: list[FairValueGap], order_blocks: list[OrderBlock]
) -> list[ZoneSpec]:
    specs = [
        ZoneSpec(
            key=("fvg", f.kind, round(f.top, 2), round(f.bottom, 2)),
            kind=f.kind,
            top=f.top,
            bottom=f.bottom,
            label="fair value gap",
            already_touched=f.mitigated,
        )
        for f in fvgs
    ]
    specs += [
        ZoneSpec(
            key=("ob", o.kind, round(o.top, 2), round(o.bottom, 2)),
            kind=o.kind,
            top=o.top,
            bottom=o.bottom,
            label="order block",
            already_touched=o.mitigated,
        )
        for o in order_blocks
    ]
    return specs


class ZoneAlertEngine:
    def __init__(self, disrespect_buffer_pct: float = DEFAULT_DISRESPECT_BUFFER_PCT) -> None:
        self.disrespect_buffer_pct = disrespect_buffer_pct
        self._states: dict[tuple, _ZoneState] = {}

    def sync_zones(self, specs: list[ZoneSpec]) -> None:
        """Register any zones not already being tracked. Never resets live state
        of a zone already known -- that would erase real-time touch history."""
        for spec in specs:
            if spec.key in self._states:
                continue
            # A zone already shown as mitigated by closed-candle history starts
            # pre-seeded as touched once, so alerts only fire for genuinely new
            # real-time developments rather than replaying chart history.
            self._states[spec.key] = _ZoneState(
                spec=spec,
                inside=spec.already_touched,
                touch_count=1 if spec.already_touched else 0,
            )

    def on_price(self, price: float, timestamp_ms: int | None = None) -> list[Alert]:
        alerts: list[Alert] = []
        for state in self._states.values():
            alert = self._update_zone(state, price, timestamp_ms)
            if alert is not None:
                alerts.append(alert)
        return alerts

    def _update_zone(
        self, state: _ZoneState, price: float, timestamp_ms: int | None
    ) -> Alert | None:
        if state.disrespected:
            return None

        spec = state.spec
        buffer = spec.top * self.disrespect_buffer_pct
        if spec.kind == "bullish":
            broke_far_edge = price < spec.bottom - buffer
        else:
            broke_far_edge = price > spec.top + buffer

        if broke_far_edge:
            state.disrespected = True
            state.inside = False
            return Alert(
                event="disrespect",
                kind=spec.kind,
                label=spec.label,
                top=spec.top,
                bottom=spec.bottom,
                price=price,
                timestamp_ms=timestamp_ms,
                message=(
                    f"DISRESPECTED {spec.kind} {spec.label} "
                    f"[{spec.bottom:.2f}-{spec.top:.2f}] -- price broke through at {price:.2f}"
                ),
            )

        near_inside = spec.bottom <= price <= spec.top
        if near_inside and not state.inside:
            state.inside = True
            state.touch_count += 1
            event: Event = "touch" if state.touch_count == 1 else "retest"
            verb = "touched" if event == "touch" else "retested"
            return Alert(
                event=event,
                kind=spec.kind,
                label=spec.label,
                top=spec.top,
                bottom=spec.bottom,
                price=price,
                timestamp_ms=timestamp_ms,
                message=(
                    f"{verb.capitalize()} {spec.kind} {spec.label} "
                    f"[{spec.bottom:.2f}-{spec.top:.2f}] at {price:.2f}"
                ),
            )

        if not near_inside and state.inside:
            state.inside = False  # exited without breaking the far edge: zone held

        return None
