"""Smart-money-concepts detection: fair value gaps and order blocks.

These are heuristic, from-scratch implementations of the standard ICT-style
definitions. They are not a reproduction of any proprietary indicator.
"""

from __future__ import annotations

from .models import Candle, ConfluenceZone, FairValueGap, Kind, OrderBlock, TouchType


def find_fair_value_gaps(candles: list[Candle]) -> list[FairValueGap]:
    """3-candle imbalance: candle[i-2] and candle[i] don't overlap, leaving a gap."""
    gaps: list[FairValueGap] = []
    for i in range(2, len(candles)):
        first, third = candles[i - 2], candles[i]
        if first.high < third.low:
            gaps.append(
                FairValueGap(
                    kind="bullish",
                    top=third.low,
                    bottom=first.high,
                    index=i,
                    timestamp_ms=third.timestamp_ms,
                )
            )
        elif first.low > third.high:
            gaps.append(
                FairValueGap(
                    kind="bearish",
                    top=first.low,
                    bottom=third.high,
                    index=i,
                    timestamp_ms=third.timestamp_ms,
                )
            )
    _mark_fvg_mitigation(gaps, candles)
    return gaps


def _classify_touch(kind: Kind, top: float, bottom: float, candle: Candle) -> TouchType:
    """The candle that first trades back into a zone either closes back
    outside it (a retest -- the level held) or closes all the way through
    to the far side (a disrespect -- the level broke)."""
    if kind == "bullish":
        return "retest" if candle.close >= bottom else "disrespect"
    return "retest" if candle.close <= top else "disrespect"


def _mark_fvg_mitigation(gaps: list[FairValueGap], candles: list[Candle]) -> None:
    for gap in gaps:
        for candle in candles[gap.index + 1 :]:
            if gap.kind == "bullish" and candle.low <= gap.top:
                gap.mitigated = True
                gap.mitigation_type = _classify_touch(gap.kind, gap.top, gap.bottom, candle)
                break
            if gap.kind == "bearish" and candle.high >= gap.bottom:
                gap.mitigated = True
                gap.mitigation_type = _classify_touch(gap.kind, gap.top, gap.bottom, candle)
                break


def _swing_points(candles: list[Candle], lookback: int) -> tuple[list[int], list[int]]:
    highs: list[int] = []
    lows: list[int] = []
    for i in range(lookback, len(candles) - lookback):
        window = candles[i - lookback : i + lookback + 1]
        if candles[i].high == max(c.high for c in window):
            highs.append(i)
        if candles[i].low == min(c.low for c in window):
            lows.append(i)
    return highs, lows


def _most_recent_before(indices: list[int], i: int) -> int | None:
    candidates = [idx for idx in indices if idx < i]
    return max(candidates) if candidates else None


def find_order_blocks(candles: list[Candle], swing_lookback: int = 2) -> list[OrderBlock]:
    """Last opposite-colour candle before a move that (a) breaks the most
    recent swing point and (b) leaves a fair value gap immediately behind it
    -- i.e. the order block candle's range doesn't overlap the candle two
    bars later. That gap is what marks genuine imbalance/institutional
    participation at that candle; a same-direction break with no gap behind
    it is not treated as a valid order block, only a naive swing break."""
    swing_highs, swing_lows = _swing_points(candles, swing_lookback)
    blocks: list[OrderBlock] = []
    for i in range(1, len(candles) - 1):  # need candles[i + 1] to confirm the gap
        prev, cur, nxt = candles[i - 1], candles[i], candles[i + 1]

        if prev.is_bearish and cur.is_bullish:
            swing = _most_recent_before(swing_highs, i)
            if swing is not None and cur.close > candles[swing].high and prev.high < nxt.low:
                blocks.append(
                    OrderBlock(
                        kind="bullish",
                        top=prev.high,
                        bottom=prev.low,
                        index=i - 1,
                        timestamp_ms=prev.timestamp_ms,
                    )
                )

        if prev.is_bullish and cur.is_bearish:
            swing = _most_recent_before(swing_lows, i)
            if swing is not None and cur.close < candles[swing].low and prev.low > nxt.high:
                blocks.append(
                    OrderBlock(
                        kind="bearish",
                        top=prev.high,
                        bottom=prev.low,
                        index=i - 1,
                        timestamp_ms=prev.timestamp_ms,
                    )
                )

    _mark_ob_mitigation(blocks, candles)
    return blocks


def _mark_ob_mitigation(blocks: list[OrderBlock], candles: list[Candle]) -> None:
    for block in blocks:
        for candle in candles[block.index + 1 :]:
            if block.kind == "bullish" and candle.low <= block.top:
                block.mitigated = True
                block.mitigation_type = _classify_touch(block.kind, block.top, block.bottom, candle)
                break
            if block.kind == "bearish" and candle.high >= block.bottom:
                block.mitigated = True
                block.mitigation_type = _classify_touch(block.kind, block.top, block.bottom, candle)
                break


def fvg_is_tradeable(fvg: FairValueGap) -> bool:
    """Both a clean retest and a disrespected (closed-straight-through)
    touch are still valid FVG trade triggers -- the thesis for trading a
    fair value gap is just that the inefficiency gets filled and reacted
    to, and a violent overshoot through it doesn't undo that. An untouched
    gap is tradeable on its first approach too, so this is unconditionally
    true; it exists so that policy is one named, greppable decision rather
    than an absent filter that reads like an oversight."""
    return True


def order_block_is_tradeable(block: OrderBlock) -> bool:
    """An order block only gets a second look on a clean retest -- price
    wicking into it and closing back out. A disrespected block (a candle
    closing straight through it) means the level failed as structure and
    is excluded for good, unlike a fair value gap. An untouched block is
    tradeable on its first approach."""
    return not block.mitigated or block.mitigation_type == "retest"


def find_confluence_zones(
    fvgs: list[FairValueGap], order_blocks: list[OrderBlock]
) -> list[ConfluenceZone]:
    """Tradeable FVG + order block of the same bias whose ranges overlap
    (see fvg_is_tradeable / order_block_is_tradeable for what "tradeable"
    means for each)."""
    zones: list[ConfluenceZone] = []
    for kind in ("bullish", "bearish"):
        live_fvgs = [f for f in fvgs if f.kind == kind and fvg_is_tradeable(f)]
        live_obs = [o for o in order_blocks if o.kind == kind and order_block_is_tradeable(o)]
        for fvg in live_fvgs:
            for ob in live_obs:
                overlap_top = min(fvg.top, ob.top)
                overlap_bottom = max(fvg.bottom, ob.bottom)
                if overlap_bottom <= overlap_top:
                    zones.append(
                        ConfluenceZone(
                            kind=kind,
                            top=overlap_top,
                            bottom=overlap_bottom,
                            fvg=fvg,
                            order_block=ob,
                        )
                    )
    return zones
