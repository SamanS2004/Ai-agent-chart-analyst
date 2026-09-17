import time

from solana_agent.agent import SolanaMemecoinAgent
from solana_agent.journal import SolanaJournal
from solana_agent.models import TokenPair
from solana_agent.signals import SignalEngine, SignalThresholds
from solana_agent.tracker import PairTracker

ONE_YEAR_AGO_MS = int(time.time() * 1000) - 365 * 86_400_000


def _pair(
    address="Token1",
    pair_address="Pair1",
    price=1.0,
    volume_m5=500.0,
    price_change_h1=12.0,
    pair_created_at_ms=ONE_YEAR_AGO_MS,
):
    return TokenPair(
        chain_id="solana",
        dex_id="raydium",
        pair_address=pair_address,
        base_token_address=address,
        base_symbol="COIN",
        base_name="Coin",
        price_usd=price,
        liquidity_usd=10_000.0,
        fdv=None,
        volume_m5=volume_m5,
        volume_h1=1200.0,
        volume_h6=0.0,
        volume_h24=5_000.0,
        price_change_m5=0.0,
        price_change_h1=price_change_h1,
        price_change_h6=0.0,
        price_change_h24=0.0,
        pair_created_at_ms=pair_created_at_ms,
        url="https://dexscreener.com/solana/pair1",
    )


class _FakeClient:
    def __init__(self, watchlist_pairs):
        self._pairs = watchlist_pairs
        self.requested_addresses = None

    def get_latest_boosted_tokens(self, chain_id="solana"):
        return []

    def get_top_boosted_tokens(self, chain_id="solana"):
        return []

    def get_latest_token_profiles(self, chain_id="solana"):
        return []

    def get_pairs_for_tokens(self, chain_id, addresses):
        self.requested_addresses = addresses
        return self._pairs


def _agent(pairs, tmp_path, watchlist=None):
    client = _FakeClient(pairs)
    journal = SolanaJournal(tmp_path)
    tracker = PairTracker()
    engine = SignalEngine(SignalThresholds(gain_min_pct=10.0, volume_multiplier=2.0))
    return SolanaMemecoinAgent(
        client=client,
        tracker=tracker,
        engine=engine,
        journal=journal,
        watchlist=watchlist or ["Token1"],
        use_boosted=False,
        use_profiles=False,
        min_liquidity_usd=1_000.0,
        min_volume_h24_usd=100.0,
    )


def test_run_once_fires_alert_for_qualifying_pair(tmp_path):
    agent = _agent([_pair(price_change_h1=12.0, volume_m5=500.0)], tmp_path)

    tracked_list, alerts = agent.run_once()

    assert len(tracked_list) == 1
    assert len(alerts) == 1
    assert alerts[0].symbol == "COIN"


def test_run_once_no_alert_when_pair_does_not_qualify(tmp_path):
    agent = _agent([_pair(price_change_h1=2.0, volume_m5=1.0)], tmp_path)

    _tracked_list, alerts = agent.run_once()

    assert alerts == []


def test_poll_and_signal_prunes_state_for_pairs_no_longer_discovered(tmp_path):
    agent = _agent([_pair()], tmp_path)
    agent.poll_and_signal(["Token1"], on_alert=lambda a: None)
    assert agent.tracker.known_pair_addresses() == ["Pair1"]

    # Second poll resolves to nothing (token dropped out of discovery)
    agent.client._pairs = []
    agent.poll_and_signal(["Token1"], on_alert=lambda a: None)

    assert agent.tracker.known_pair_addresses() == []


def test_investability_filter_applies_before_tracking(tmp_path):
    dust = _pair(pair_address="Dust", volume_m5=0.0)
    agent = _agent([dust], tmp_path)
    agent.min_volume_h24_usd = 10_000.0  # above the fixture's volume_h24 of 5000

    tracked_list, alerts = agent.run_once()

    assert tracked_list == []
    assert alerts == []


def test_new_pair_is_excluded_by_default_even_if_otherwise_qualifying(tmp_path):
    fresh = _pair(price_change_h1=12.0, volume_m5=500.0, pair_created_at_ms=int(time.time() * 1000))
    agent = _agent([fresh], tmp_path)

    tracked_list, alerts = agent.run_once()

    assert tracked_list == []
    assert alerts == []


def test_new_pair_is_included_when_age_filter_disabled(tmp_path):
    fresh = _pair(price_change_h1=12.0, volume_m5=500.0, pair_created_at_ms=int(time.time() * 1000))
    agent = _agent([fresh], tmp_path)
    agent.min_pair_age_days = 0

    tracked_list, alerts = agent.run_once()

    assert len(tracked_list) == 1
    assert len(alerts) == 1
