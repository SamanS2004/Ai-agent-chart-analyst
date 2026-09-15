from trading_agent.models import Candle
from trading_agent.smc import find_confluence_zones, find_fair_value_gaps, find_order_blocks


def candle(ts, o, h, l, cl, v=1.0):
    return Candle(timestamp_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


def test_bullish_fvg_detected():
    candles = [
        candle(0, 100, 101, 99, 100.5),
        candle(1, 100.5, 110, 100, 109),
        candle(2, 109, 115, 108, 114),  # low(108) > first.high(101) -> gap
    ]
    gaps = find_fair_value_gaps(candles)
    assert len(gaps) == 1
    assert gaps[0].kind == "bullish"
    assert gaps[0].bottom == 101
    assert gaps[0].top == 108


def test_bearish_fvg_detected():
    candles = [
        candle(0, 100, 101, 99, 99.5),
        candle(1, 99.5, 100, 90, 91),
        candle(2, 91, 92, 85, 86),  # high(92) < first.low(99) -> gap
    ]
    gaps = find_fair_value_gaps(candles)
    assert len(gaps) == 1
    assert gaps[0].kind == "bearish"
    assert gaps[0].top == 99
    assert gaps[0].bottom == 92


def test_no_gap_when_candles_overlap():
    candles = [
        candle(0, 100, 105, 99, 102),
        candle(1, 102, 108, 101, 106),
        candle(2, 106, 110, 100, 104),  # overlaps candle 0's range
    ]
    assert find_fair_value_gaps(candles) == []


def test_fvg_mitigation_when_price_returns():
    candles = [
        candle(0, 100, 101, 99, 100.5),
        candle(1, 100.5, 110, 100, 109),
        candle(2, 109, 115, 108, 114),
        candle(3, 114, 116, 105, 110),  # low(105) dips back into the [101, 108] gap
    ]
    gaps = find_fair_value_gaps(candles)
    assert gaps[0].mitigated is True


def test_fvg_not_mitigated_when_price_stays_away():
    candles = [
        candle(0, 100, 101, 99, 100.5),
        candle(1, 100.5, 110, 100, 109),
        candle(2, 109, 115, 108, 114),
        candle(3, 114, 118, 112, 116),  # low(112) stays above the gap top(108)
    ]
    gaps = find_fair_value_gaps(candles)
    assert gaps[0].mitigated is False


def _structure_break_candles():
    return [
        candle(0, 100, 100, 99, 99.5),
        candle(1, 99.5, 101, 99, 100.5),
        candle(2, 100.5, 105, 100, 104),  # swing high at index 2 (high=105)
        candle(3, 104, 103, 102, 102.5),
        candle(4, 102.5, 104, 101, 101.5),
        candle(5, 101.5, 102, 99, 99.5),  # last bearish candle before breakout
        candle(6, 103, 109, 103, 108),  # breakout: close(108) > swing high(105), stays above the OB
        candle(7, 108, 110, 107, 109.5),
        candle(8, 109.5, 111, 108, 110),
    ]


def test_bullish_order_block_detected_on_structure_break():
    blocks = find_order_blocks(_structure_break_candles())
    bullish = [b for b in blocks if b.kind == "bullish"]
    assert len(bullish) == 1
    assert bullish[0].index == 5
    assert bullish[0].top == 102
    assert bullish[0].bottom == 99


def test_order_block_not_mitigated_before_price_returns():
    candles = _structure_break_candles()[:7]  # stop right after formation, no retrace yet
    blocks = find_order_blocks(candles)
    bullish = [b for b in blocks if b.kind == "bullish"]
    assert len(bullish) == 1
    assert bullish[0].mitigated is False


def test_confluence_zone_requires_overlap_of_same_bias():
    candles = _structure_break_candles()
    fvgs = find_fair_value_gaps(candles)
    order_blocks = find_order_blocks(candles)
    zones = find_confluence_zones(fvgs, order_blocks)
    for zone in zones:
        assert zone.top >= zone.bottom
        assert zone.fvg.kind == zone.kind == zone.order_block.kind
