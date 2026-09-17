from trading_agent.models import Candle, FairValueGap, OrderBlock
from trading_agent.strategy import generate_trade_idea


def candle(ts, o, h, l, cl, v=1.0):
    return Candle(timestamp_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


def test_long_idea_from_confluence_zone():
    candles = [candle(0, 100, 101, 99, 100.5), candle(1, 101, 102, 100, 101.5)]
    price = candles[-1].close  # 101.5
    fvg = FairValueGap(kind="bullish", top=100, bottom=98, index=0, timestamp_ms=0)
    ob = OrderBlock(kind="bullish", top=99.5, bottom=98.5, index=0, timestamp_ms=0)

    from trading_agent.smc import find_confluence_zones

    zones = find_confluence_zones([fvg], [ob])
    idea = generate_trade_idea(candles, [fvg], [ob], zones)

    assert idea is not None
    assert idea.kind == "long"
    assert idea.confidence == "high"
    assert idea.stop < idea.entry < price
    assert all(t > idea.entry for t in idea.targets)


def test_short_idea_from_single_fvg_when_no_confluence():
    candles = [candle(0, 100, 101, 99, 100.5), candle(1, 100, 101, 99, 98)]
    price = candles[-1].close  # 98
    # top/bottom placed above the range's equilibrium (100) so this bearish
    # gap sits in premium, as required for it to be a valid candidate.
    fvg = FairValueGap(kind="bearish", top=102, bottom=101, index=0, timestamp_ms=0)

    idea = generate_trade_idea(candles, [fvg], [], [])

    assert idea is not None
    assert idea.kind == "short"
    assert idea.confidence == "medium"
    assert idea.entry > price
    assert idea.stop > idea.entry
    assert all(t < idea.entry for t in idea.targets)


def test_no_idea_when_no_zones():
    candles = [candle(0, 100, 101, 99, 100.5)]
    assert generate_trade_idea(candles, [], [], []) is None


def test_mitigated_fvg_still_produces_an_idea_on_retest():
    # FVGs aren't excluded by mitigation status at all -- both a retest and
    # a disrespect are valid triggers, per the strategy rule.
    candles = [candle(0, 100, 101, 99, 100.5)]
    price = candles[-1].close  # 100.5
    fvg = FairValueGap(
        kind="bullish", top=99, bottom=97, index=0, timestamp_ms=0, mitigated=True, mitigation_type="retest"
    )
    idea = generate_trade_idea(candles, [fvg], [], [])
    assert idea is not None
    assert idea.zone_type == "fair value gap"
    assert idea.entry < price


def test_fvg_ignored_once_price_has_passed_it_regardless_of_mitigation():
    # The real remaining filter is geometric approachability (is the zone
    # still on the correct side of current price), independent of whether
    # or how it was touched.
    candles = [candle(0, 100, 101, 99, 100.5)]
    # mid(99) still clears the discount/premium filter -- top(101) > price(100.5)
    # is what excludes it, isolating the approachability check specifically.
    fvg = FairValueGap(kind="bullish", top=101, bottom=97, index=0, timestamp_ms=0)
    assert generate_trade_idea(candles, [fvg], [], []) is None


def test_order_block_idea_still_uses_a_retested_block():
    candles = [candle(0, 100, 101, 99, 100.5), candle(1, 100, 101, 99, 98)]
    ob = OrderBlock(
        kind="bearish", top=102, bottom=101, index=0, timestamp_ms=0, mitigated=True, mitigation_type="retest"
    )
    idea = generate_trade_idea(candles, [], [ob], [])
    assert idea is not None
    assert idea.zone_type == "order block"


def test_order_block_idea_skips_a_disrespected_block():
    candles = [candle(0, 100, 101, 99, 100.5), candle(1, 100, 101, 99, 98)]
    ob = OrderBlock(
        kind="bearish", top=102, bottom=101, index=0, timestamp_ms=0, mitigated=True, mitigation_type="disrespect"
    )
    assert generate_trade_idea(candles, [], [ob], []) is None
