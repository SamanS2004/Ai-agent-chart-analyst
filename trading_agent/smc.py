"""Smart-money-concepts detection: fair value gaps and order blocks.

These are heuristic, from-scratch implementations of the standard ICT-style
definitions. They are not a reproduction of any proprietary indicator.
"""

from __future__ import annotations

from .models import Candle, ConfluenceZone, FairValueGap, OrderBlock


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


def _mark_fvg_mitigation(gaps: list[FairValueGap], candles: list[Candle]) -> None:
    for gap in gaps:
        for candle in candles[gap.index + 1 :]:
            if gap.kind == "bullish" and candle.low <= gap.top:
                gap.mitigated = True
                break
            if gap.kind == "bearish" and candle.high >= gap.bottom:
                gap.mitigated = True
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
    """Last opposite-colour candle before a move that breaks the most recent swing point."""
    swing_highs, swing_lows = _swing_points(candles, swing_lookback)
    blocks: list[OrderBlock] = []
    for i in range(1, len(candles)):
        prev, cur = candles[i - 1], candles[i]

        if prev.is_bearish and cur.is_bullish:
            swing = _most_recent_before(swing_highs, i)
            if swing is not None and cur.close > candles[swing].high:
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
            if swing is not None and cur.close < candles[swing].low:
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
                break
            if block.kind == "bearish" and candle.high >= block.bottom:
                block.mitigated = True
                break


def find_confluence_zones(
    fvgs: list[FairValueGap], order_blocks: list[OrderBlock]
) -> list[ConfluenceZone]:
    """Unmitigated FVG + order block of the same bias whose ranges overlap."""
    zones: list[ConfluenceZone] = []
    for kind in ("bullish", "bearish"):
        live_fvgs = [f for f in fvgs if f.kind == kind and not f.mitigated]
        live_obs = [o for o in order_blocks if o.kind == kind and not o.mitigated]
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
