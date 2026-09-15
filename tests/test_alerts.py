from trading_agent.alerts import (
    ZoneAlertEngine,
    ZoneSpec,
    filter_zones_near_price,
    zone_specs_from_detections,
)
from trading_agent.models import FairValueGap, OrderBlock


def bullish_zone(key="z1", top=100, bottom=98):
    return ZoneSpec(key=key, kind="bullish", top=top, bottom=bottom, label="fair value gap")


def bearish_zone(key="z1", top=102, bottom=100):
    return ZoneSpec(key=key, kind="bearish", top=top, bottom=bottom, label="fair value gap")


def test_first_touch_fires_touch_event():
    engine = ZoneAlertEngine()
    engine.sync_zones([bullish_zone()])

    alerts = engine.on_price(105)  # above the zone: no event yet
    assert alerts == []

    alerts = engine.on_price(99)  # enters [98, 100]
    assert len(alerts) == 1
    assert alerts[0].event == "touch"


def test_second_touch_after_leaving_is_a_retest():
    engine = ZoneAlertEngine()
    engine.sync_zones([bullish_zone()])

    engine.on_price(99)  # touch
    engine.on_price(105)  # leaves the zone, held
    alerts = engine.on_price(99.5)  # comes back

    assert len(alerts) == 1
    assert alerts[0].event == "retest"


def test_staying_inside_the_zone_does_not_refire():
    engine = ZoneAlertEngine()
    engine.sync_zones([bullish_zone()])

    engine.on_price(99)
    alerts = engine.on_price(98.5)  # still inside [98, 100]

    assert alerts == []


def test_bullish_disrespect_when_price_breaks_below_bottom():
    engine = ZoneAlertEngine()
    engine.sync_zones([bullish_zone(top=100, bottom=98)])

    alerts = engine.on_price(97)  # below bottom(98) beyond buffer
    assert len(alerts) == 1
    assert alerts[0].event == "disrespect"


def test_bearish_disrespect_when_price_breaks_above_top():
    engine = ZoneAlertEngine()
    engine.sync_zones([bearish_zone(top=102, bottom=100)])

    alerts = engine.on_price(103.5)
    assert len(alerts) == 1
    assert alerts[0].event == "disrespect"


def test_disrespected_zone_never_alerts_again():
    engine = ZoneAlertEngine()
    engine.sync_zones([bullish_zone(top=100, bottom=98)])

    engine.on_price(97)  # disrespect
    alerts = engine.on_price(99)  # price wanders back in -- zone is retired

    assert alerts == []


def test_small_wick_within_buffer_does_not_count_as_disrespect():
    engine = ZoneAlertEngine(disrespect_buffer_pct=0.01)  # 1% buffer for this test
    engine.sync_zones([bullish_zone(top=100, bottom=98)])

    alerts = engine.on_price(97.5)  # just past bottom, inside the buffer
    assert alerts == []


def test_sync_does_not_reset_existing_live_state():
    engine = ZoneAlertEngine()
    spec = bullish_zone()
    engine.sync_zones([spec])
    engine.on_price(99)  # touch, touch_count -> 1

    engine.sync_zones([spec])  # recompute cycle re-registers the same zone
    alerts = engine.on_price(99.5)  # still inside, should not re-fire touch

    assert alerts == []


def test_already_mitigated_zone_is_pre_seeded_so_it_does_not_replay_history():
    spec = ZoneSpec(
        key="z1", kind="bullish", top=100, bottom=98, label="fair value gap", already_touched=True
    )
    engine = ZoneAlertEngine()
    engine.sync_zones([spec])

    # Price is already inside the zone (as closed-candle history showed); no
    # touch alert should fire purely from registering it.
    alerts = engine.on_price(99)
    assert alerts == []

    # Leaving and coming back now correctly counts as a retest.
    engine.on_price(105)
    alerts = engine.on_price(99)
    assert len(alerts) == 1
    assert alerts[0].event == "retest"


def test_filter_zones_near_price_keeps_nearby_and_straddling_zones():
    price = 100.0
    near = bullish_zone(key="near", top=99, bottom=97)  # 1% away
    far = bullish_zone(key="far", top=50, bottom=48)  # way below price
    straddling = ZoneSpec(key="straddle", kind="bullish", top=101, bottom=99, label="x")

    kept = filter_zones_near_price([near, far, straddling], price, proximity_pct=0.05)

    assert {s.key for s in kept} == {"near", "straddle"}


def test_filter_zones_near_price_disabled_when_pct_is_zero():
    price = 100.0
    far = bullish_zone(key="far", top=50, bottom=48)
    assert filter_zones_near_price([far], price, proximity_pct=0) == [far]


def test_zone_specs_from_detections_maps_fvgs_and_order_blocks():
    fvg = FairValueGap(kind="bullish", top=100, bottom=98, index=0, timestamp_ms=0)
    ob = OrderBlock(kind="bearish", top=105, bottom=103, index=1, timestamp_ms=0)

    specs = zone_specs_from_detections([fvg], [ob])

    labels = {s.label for s in specs}
    assert labels == {"fair value gap", "order block"}
    assert len({s.key for s in specs}) == 2  # keys are distinct
