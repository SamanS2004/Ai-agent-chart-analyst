"""Turns detected FVG/order-block zones into a single trade idea, if one qualifies."""

from __future__ import annotations

from .models import Candle, ConfluenceZone, FairValueGap, OrderBlock, PriorityZone, TradeIdea
from .stacking import resolve_fvg_stacks

_MIN_R_MULTIPLE_TARGETS = (2.0, 3.0)
_STOP_BUFFER_PCT = 0.001  # 0.1% beyond the zone edge


def generate_trade_idea(
    candles: list[Candle],
    fvgs: list[FairValueGap],
    order_blocks: list[OrderBlock],
    confluence_zones: list[ConfluenceZone],
) -> TradeIdea | None:
    if not candles:
        return None
    price = candles[-1].close
    timestamp_ms = candles[-1].timestamp_ms
    range_high = max(c.high for c in candles)
    range_low = min(c.low for c in candles)

    stack_zones = resolve_fvg_stacks(fvgs, confluence_zones, price, range_high, range_low)

    confluence_zones_resolved = [z for z in stack_zones if z.has_confluence]
    if confluence_zones_resolved:
        best = min(confluence_zones_resolved, key=lambda z: _distance(z.kind, z.top, z.bottom, price))
        return _idea_from_priority_zone(
            best, price, timestamp_ms, confidence="high", zone_type="fvg+order_block confluence"
        )

    plain_zones = [z for z in stack_zones if not z.has_confluence]
    if plain_zones:
        best = min(plain_zones, key=lambda z: _distance(z.kind, z.top, z.bottom, price))
        return _idea_from_priority_zone(
            best, price, timestamp_ms, confidence="medium", zone_type="fair value gap"
        )

    best_ob = _nearest_order_block(order_blocks, price)
    if best_ob is not None:
        return _idea_from_zone(
            kind=best_ob.kind,
            top=best_ob.top,
            bottom=best_ob.bottom,
            price=price,
            timestamp_ms=timestamp_ms,
            confidence="medium",
            zone_type="order block",
        )

    return None


def _nearest_order_block(order_blocks: list[OrderBlock], price: float) -> OrderBlock | None:
    candidates = [o for o in order_blocks if not o.mitigated and _zone_is_approachable(o.kind, o.top, o.bottom, price)]
    if not candidates:
        return None
    return min(candidates, key=lambda o: _distance(o.kind, o.top, o.bottom, price))


def _zone_is_approachable(kind: str, top: float, bottom: float, price: float) -> bool:
    # A bullish zone sits below price (support to buy a retracement into);
    # a bearish zone sits above price (resistance to sell a retracement into).
    if kind == "bullish":
        return top <= price
    return bottom >= price


def _distance(kind: str, top: float, bottom: float, price: float) -> float:
    return price - top if kind == "bullish" else bottom - price


def _idea_from_priority_zone(
    zone: PriorityZone,
    price: float,
    timestamp_ms: int,
    confidence: str,
    zone_type: str,
) -> TradeIdea:
    note = ""
    if zone.stack_size > 1:
        note = f" Nearest of {zone.stack_size} stacked gaps from the same push"
        note += ", the widest one in it." if zone.is_widest_in_stack else "."
    return _idea_from_zone(
        kind=zone.kind,
        top=zone.top,
        bottom=zone.bottom,
        price=price,
        timestamp_ms=timestamp_ms,
        confidence=confidence,
        zone_type=zone_type,
        extra_note=note,
    )


def _idea_from_zone(
    kind: str,
    top: float,
    bottom: float,
    price: float,
    timestamp_ms: int,
    confidence: str,
    zone_type: str,
    extra_note: str = "",
) -> TradeIdea:
    zone_range = top - bottom
    buffer = max(zone_range * 0.1, top * _STOP_BUFFER_PCT)

    if kind == "bullish":
        entry = top
        stop = bottom - buffer
        risk = entry - stop
        targets = [entry + risk * r for r in _MIN_R_MULTIPLE_TARGETS]
        trade_kind = "long"
    else:
        entry = bottom
        stop = top + buffer
        risk = stop - entry
        targets = [entry - risk * r for r in _MIN_R_MULTIPLE_TARGETS]
        trade_kind = "short"

    rationale = (
        f"Price ({price:.2f}) is approaching an unmitigated {kind} {zone_type} "
        f"between {bottom:.2f} and {top:.2f} on the 15m chart."
        f"{extra_note} "
        f"Plan: {trade_kind} on a tap of the zone, invalidated beyond {stop:.2f}."
    )

    return TradeIdea(
        kind=trade_kind,
        entry=round(entry, 2),
        stop=round(stop, 2),
        targets=[round(t, 2) for t in targets],
        confidence=confidence,
        zone_type=zone_type,
        rationale=rationale,
        timestamp_ms=timestamp_ms,
    )
