from solana_agent.models import TokenPair, TrackedPair
from solana_agent.signals import SignalEngine, SignalThresholds


def _tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=0):
    pair = TokenPair(
        chain_id="solana",
        dex_id="raydium",
        pair_address="Pair1",
        base_token_address="Token1",
        base_symbol="COIN",
        base_name="Coin",
        price_usd=0.001,
        liquidity_usd=10_000.0,
        fdv=None,
        volume_m5=500.0,
        volume_h1=1200.0,
        volume_h6=0.0,
        volume_h24=0.0,
        price_change_m5=0.0,
        price_change_h1=gain_pct,
        price_change_h6=0.0,
        price_change_h24=0.0,
        pair_created_at_ms=None,
        url="https://dexscreener.com/solana/pair1",
    )
    return TrackedPair(
        pair=pair,
        gain_pct=gain_pct,
        gain_basis="local",
        volume_multiplier=volume_multiplier,
        volume_basis="local",
        first_seen_ms=0,
        last_updated_ms=now_ms,
    )


def test_fires_when_gain_and_volume_both_qualify():
    engine = SignalEngine()
    alerts = engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0))

    assert len(alerts) == 1
    assert alerts[0].event == "volume_and_gain"
    assert "12.0%" in alerts[0].message


def test_does_not_fire_when_gain_below_threshold():
    engine = SignalEngine()
    assert engine.evaluate(_tracked(gain_pct=5.0, volume_multiplier=5.0)) == []


def test_does_not_fire_when_volume_below_threshold():
    engine = SignalEngine()
    assert engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=1.2)) == []


def test_does_not_refire_while_still_qualifying():
    engine = SignalEngine()
    first = engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=0))
    second = engine.evaluate(_tracked(gain_pct=13.0, volume_multiplier=3.5, now_ms=1000))

    assert len(first) == 1
    assert second == []


def test_refires_after_cooling_below_reset_level_and_qualifying_again():
    thresholds = SignalThresholds(gain_min_pct=10.0, reset_buffer_pct=5.0)
    engine = SignalEngine(thresholds)

    engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=0))
    engine.evaluate(_tracked(gain_pct=4.0, volume_multiplier=3.0, now_ms=1000))  # cools below 10-5=5
    third = engine.evaluate(_tracked(gain_pct=11.0, volume_multiplier=3.0, now_ms=2000))

    assert len(third) == 1


def test_stays_armed_if_gain_dips_but_not_past_reset_buffer():
    thresholds = SignalThresholds(gain_min_pct=10.0, reset_buffer_pct=5.0)
    engine = SignalEngine(thresholds)

    engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=0))
    engine.evaluate(_tracked(gain_pct=7.0, volume_multiplier=3.0, now_ms=1000))  # dips but stays above reset(5)
    third = engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=2000))

    assert third == []


def test_require_volume_false_ignores_volume():
    thresholds = SignalThresholds(require_volume=False)
    engine = SignalEngine(thresholds)

    alerts = engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=0.1))
    assert len(alerts) == 1


def test_require_gain_false_ignores_gain():
    thresholds = SignalThresholds(require_gain=False)
    engine = SignalEngine(thresholds)

    alerts = engine.evaluate(_tracked(gain_pct=0.0, volume_multiplier=5.0))
    assert len(alerts) == 1


def test_forget_clears_latched_state():
    engine = SignalEngine()
    engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=0))
    engine.forget("Pair1")

    alerts = engine.evaluate(_tracked(gain_pct=12.0, volume_multiplier=3.0, now_ms=1000))
    assert len(alerts) == 1
