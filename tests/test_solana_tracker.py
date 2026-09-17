from solana_agent.models import TokenPair
from solana_agent.tracker import PairTracker


def _pair(price=1.0, volume_m5=100.0, volume_h1=1200.0, price_change_h1=8.0):
    return TokenPair(
        chain_id="solana",
        dex_id="raydium",
        pair_address="Pair1",
        base_token_address="Token1",
        base_symbol="COIN",
        base_name="Coin",
        price_usd=price,
        liquidity_usd=10_000.0,
        fdv=None,
        volume_m5=volume_m5,
        volume_h1=volume_h1,
        volume_h6=0.0,
        volume_h24=0.0,
        price_change_m5=0.0,
        price_change_h1=price_change_h1,
        price_change_h6=0.0,
        price_change_h24=0.0,
        pair_created_at_ms=None,
        url="",
    )


def test_first_update_falls_back_to_api_h1_fields():
    tracker = PairTracker()
    tracked = tracker.update(_pair(price=1.0, price_change_h1=8.0), now_ms=0)

    assert tracked.gain_basis == "api_h1"
    assert tracked.gain_pct == 8.0
    assert tracked.volume_basis == "api_h1_rate"


def test_local_gain_uses_recent_low_once_enough_history():
    tracker = PairTracker(min_local_span_seconds=600)

    tracker.update(_pair(price=1.0), now_ms=0)
    tracked = tracker.update(_pair(price=1.20), now_ms=700_000)  # +700s, price up 20%

    assert tracked.gain_basis == "local"
    assert round(tracked.gain_pct, 2) == 20.0


def test_local_gain_measures_from_lowest_point_not_first_point():
    tracker = PairTracker(min_local_span_seconds=600)

    tracker.update(_pair(price=1.0), now_ms=0)
    tracker.update(_pair(price=0.5), now_ms=300_000)  # dips to a new low
    tracked = tracker.update(_pair(price=0.6), now_ms=700_000)  # up from the low, not from 1.0

    assert tracked.gain_basis == "local"
    assert round(tracked.gain_pct, 2) == 20.0  # (0.6-0.5)/0.5 * 100


def test_local_volume_multiplier_uses_baseline_of_past_samples():
    tracker = PairTracker(min_samples_for_local_volume=3)

    tracker.update(_pair(volume_m5=100.0), now_ms=0)
    tracker.update(_pair(volume_m5=100.0), now_ms=60_000)
    tracked = tracker.update(_pair(volume_m5=400.0), now_ms=120_000)  # spike

    assert tracked.volume_basis == "local"
    assert tracked.volume_multiplier == 4.0


def test_volume_multiplier_capped_to_avoid_absurd_numbers():
    tracker = PairTracker()
    tracked = tracker.update(_pair(volume_m5=1_000_000.0, volume_h1=12.0), now_ms=0)

    assert tracked.volume_multiplier <= 1000.0


def test_zero_baseline_volume_with_no_recent_volume_is_not_a_spike():
    tracker = PairTracker()
    tracked = tracker.update(_pair(volume_m5=0.0, volume_h1=0.0), now_ms=0)

    assert tracked.volume_multiplier == 1.0


def test_forget_clears_history_so_next_update_restarts_baseline():
    tracker = PairTracker(min_local_span_seconds=600)
    tracker.update(_pair(price=1.0), now_ms=0)
    tracker.update(_pair(price=1.5), now_ms=700_000)

    tracker.forget("Pair1")
    tracked = tracker.update(_pair(price=1.5, price_change_h1=3.0), now_ms=800_000)

    assert tracked.gain_basis == "api_h1"
    assert tracked.gain_pct == 3.0
