"""Turns detected FVG/order-block zones into a single trade idea, if one qualifies."""

from __future__ import annotations

from .models import Candle, ConfluenceZone, FairValueGap, OrderBlock, TradeIdea

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

    best = _nearest_confluence(confluence_zones, price)
    if best is not None:
        return _idea_from_zone(
            kind=best.kind,
            top=best.top,
            bottom=best.bottom,
            price=price,
            timestamp_ms=timestamp_ms,
            confidence="high",
            zone_type="fvg+order_block confluence",
        )

    best_single = _nearest_single_zone(fvgs, order_blocks, price)
    if best_single is not None:
        kind, top, bottom, zone_type = best_single
        return _idea_from_zone(
            kind=kind,
            top=top,
            bottom=bottom,
            price=price,
            timestamp_ms=timestamp_ms,
            confidence="medium",
            zone_type=zone_type,
        )

    return None


def _nearest_confluence(zones: list[ConfluenceZone], price: float) -> ConfluenceZone | None:
    candidates = [z for z in zones if _zone_is_approachable(z.kind, z.top, z.bottom, price)]
    if not candidates:
        return None
    return min(candidates, key=lambda z: _distance(z.kind, z.top, z.bottom, price))


def _nearest_single_zone(
    fvgs: list[FairValueGap], order_blocks: list[OrderBlock], price: float
) -> tuple[str, float, float, str] | None:
    pool: list[tuple[str, float, float, str]] = []
    for f in fvgs:
        if not f.mitigated and _zone_is_approachable(f.kind, f.top, f.bottom, price):
            pool.append((f.kind, f.top, f.bottom, "fair value gap"))
    for o in order_blocks:
        if not o.mitigated and _zone_is_approachable(o.kind, o.top, o.bottom, price):
            pool.append((o.kind, o.top, o.bottom, "order block"))
    if not pool:
        return None
    return min(pool, key=lambda item: _distance(item[0], item[1], item[2], price))


def _zone_is_approachable(kind: str, top: float, bottom: float, price: float) -> bool:
    # A bullish zone sits below price (support to buy a retracement into);
    # a bearish zone sits above price (resistance to sell a retracement into).
    if kind == "bullish":
        return top <= price
    return bottom >= price


def _distance(kind: str, top: float, bottom: float, price: float) -> float:
    return price - top if kind == "bullish" else bottom - price


def _idea_from_zone(
    kind: str,
    top: float,
    bottom: float,
    price: float,
    timestamp_ms: int,
    confidence: str,
    zone_type: str,
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
        f"between {bottom:.2f} and {top:.2f} on the 15m chart. "
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
