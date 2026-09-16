from trading_agent.models import ConfluenceZone, FairValueGap, OrderBlock
from trading_agent.stacking import group_into_stacks, resolve_fvg_stacks


def fvg(kind, top, bottom, index, mitigated=False, mitigation_type=None):
    return FairValueGap(
        kind=kind,
        top=top,
        bottom=bottom,
        index=index,
        timestamp_ms=index * 1000,
        mitigated=mitigated,
        mitigation_type=mitigation_type,
    )


def test_group_into_stacks_groups_back_to_back_same_kind_gaps():
    gaps = [
        fvg("bullish", 101, 100, index=10),
        fvg("bullish", 103, 102, index=11),
        fvg("bullish", 105, 104, index=12),
    ]
    stacks = group_into_stacks(gaps)
    assert len(stacks) == 1
    assert len(stacks[0]) == 3


def test_group_into_stacks_splits_on_large_index_gap():
    gaps = [
        fvg("bullish", 101, 100, index=10),
        fvg("bullish", 103, 102, index=11),
        fvg("bullish", 130, 129, index=50),  # far away in time -- separate push
    ]
    stacks = group_into_stacks(gaps)
    assert len(stacks) == 2
    assert {len(s) for s in stacks} == {2, 1}


def test_group_into_stacks_keeps_kinds_separate():
    gaps = [
        fvg("bullish", 101, 100, index=10),
        fvg("bearish", 99, 98, index=11),
    ]
    stacks = group_into_stacks(gaps)
    assert len(stacks) == 2


def test_resolve_stack_picks_nearest_to_price_not_deepest():
    # A bullish push left three stacked gaps; price is retracing down from
    # above. The nearest one (top edge closest to price) should win, not the
    # deepest/widest one further from current price.
    stack = [
        fvg("bullish", top=120, bottom=118, index=10),  # near price -- should win
        fvg("bullish", top=110, bottom=100, index=11),  # deeper and wider
    ]
    price = 121  # just above the nearest gap, approaching it
    # range widened (vs. the discount/premium-focused tests below) so both
    # members clear the discount filter and this test isolates nearest-vs-
    # deepest selection specifically.
    zones = resolve_fvg_stacks(stack, [], price, range_high=200, range_low=90)
    assert len(zones) == 1
    assert zones[0].top == 120 and zones[0].bottom == 118
    assert zones[0].stack_size == 2


def test_resolve_stack_does_not_offer_a_deeper_gap_once_nearest_is_breached():
    # Price has traded through the nearest gap's top without reacting.
    # The deeper gap (100-110) is individually still "approachable" at this
    # price (110 <= 115) -- proving this test actually needs the
    # whole-stack-disqualified rule, not just coincidentally passing because
    # both gaps happened to be breached.
    stack = [
        fvg("bullish", top=120, bottom=118, index=10),
        fvg("bullish", top=110, bottom=100, index=11),
    ]
    price = 115
    zones = resolve_fvg_stacks(stack, [], price, range_high=200, range_low=90)
    assert zones == []


def test_resolve_stack_prefers_confluence_member_over_nearer_plain_member():
    ob = OrderBlock(kind="bullish", top=112, bottom=109, index=11, timestamp_ms=0)
    near_plain = fvg("bullish", top=120, bottom=118, index=10)
    deeper_with_ob = fvg("bullish", top=112, bottom=109, index=11)
    confluence = [ConfluenceZone(kind="bullish", top=112, bottom=109, fvg=deeper_with_ob, order_block=ob)]

    zones = resolve_fvg_stacks(
        [near_plain, deeper_with_ob], confluence, price=121, range_high=200, range_low=90
    )
    assert len(zones) == 1
    assert zones[0].has_confluence is True
    assert zones[0].top == 112 and zones[0].bottom == 109
    assert zones[0].order_block is ob


def test_resolve_stack_confluence_does_not_rescue_an_already_breached_stack():
    # The near gap is breached; a deeper gap has OB confluence. Confluence
    # upgrades which *live* zone you react at -- it doesn't let you reach
    # past an already-failed near gap to grab a "better" one behind it.
    ob = OrderBlock(kind="bullish", top=112, bottom=109, index=11, timestamp_ms=0)
    near_plain = fvg("bullish", top=120, bottom=118, index=10)
    deeper_with_ob = fvg("bullish", top=112, bottom=109, index=11)
    confluence = [ConfluenceZone(kind="bullish", top=112, bottom=109, fvg=deeper_with_ob, order_block=ob)]

    zones = resolve_fvg_stacks(
        [near_plain, deeper_with_ob], confluence, price=115, range_high=200, range_low=90
    )
    assert zones == []


def test_resolve_stack_drops_gap_that_fails_discount_premium():
    # Range 90-130, equilibrium 110. A bullish gap must sit at or below
    # equilibrium (discount) to be valid -- this one's mid (125) is well
    # into premium, so it should be dropped even though otherwise valid.
    stack = [fvg("bullish", top=126, bottom=124, index=10)]
    zones = resolve_fvg_stacks(stack, [], price=127, range_high=130, range_low=90)
    assert zones == []


def test_resolve_stack_keeps_gap_that_passes_discount_premium():
    # Same range, but this gap's mid (95) is in the lower half (discount).
    stack = [fvg("bullish", top=96, bottom=94, index=10)]
    zones = resolve_fvg_stacks(stack, [], price=97, range_high=130, range_low=90)
    assert len(zones) == 1


def test_resolve_stack_marks_widest_member():
    stack = [
        fvg("bullish", top=120, bottom=119, index=10),  # narrow, 1 wide
        fvg("bullish", top=112, bottom=100, index=11),  # wide, 12 wide
    ]
    # price approaching only the narrow (nearer) one
    zones = resolve_fvg_stacks(stack, [], price=121, range_high=200, range_low=90)
    assert len(zones) == 1
    assert zones[0].is_widest_in_stack is False  # the selected (nearest) one isn't the widest


def test_resolve_stack_includes_a_retested_gap():
    # FVGs are tradeable on a retest, not just their first approach.
    stack = [fvg("bullish", top=96, bottom=94, index=10, mitigated=True, mitigation_type="retest")]
    zones = resolve_fvg_stacks(stack, [], price=97, range_high=130, range_low=90)
    assert len(zones) == 1


def test_resolve_stack_includes_a_disrespected_gap():
    # And on a disrespect too -- filling the gap is the thesis either way.
    stack = [fvg("bullish", top=96, bottom=94, index=10, mitigated=True, mitigation_type="disrespect")]
    zones = resolve_fvg_stacks(stack, [], price=97, range_high=130, range_low=90)
    assert len(zones) == 1
