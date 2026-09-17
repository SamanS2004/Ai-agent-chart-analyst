from solana_agent.discovery import (
    best_pair_per_token,
    discover_candidate_addresses,
    discover_pairs,
    filter_investable,
)
from solana_agent.models import TokenPair


def _pair(address="Token1", pair_address="Pair1", liquidity=10_000.0, volume_h24=5_000.0):
    return TokenPair(
        chain_id="solana",
        dex_id="raydium",
        pair_address=pair_address,
        base_token_address=address,
        base_symbol="COIN",
        base_name="Coin",
        price_usd=0.001,
        liquidity_usd=liquidity,
        fdv=100_000.0,
        volume_m5=100.0,
        volume_h1=600.0,
        volume_h6=2000.0,
        volume_h24=volume_h24,
        price_change_m5=1.0,
        price_change_h1=5.0,
        price_change_h6=10.0,
        price_change_h24=15.0,
        pair_created_at_ms=None,
        url="https://dexscreener.com/solana/pair1",
    )


def test_best_pair_per_token_keeps_highest_liquidity():
    low = _pair(address="Token1", pair_address="Low", liquidity=1_000.0)
    high = _pair(address="Token1", pair_address="High", liquidity=50_000.0)
    other = _pair(address="Token2", pair_address="Other", liquidity=2_000.0)

    best = best_pair_per_token([low, high, other])

    addresses = {p.pair_address for p in best}
    assert addresses == {"High", "Other"}


def test_filter_investable_drops_low_liquidity_and_dust_volume():
    good = _pair(liquidity=10_000.0, volume_h24=5_000.0)
    low_liquidity = _pair(pair_address="LowLiq", liquidity=100.0, volume_h24=5_000.0)
    low_volume = _pair(pair_address="LowVol", liquidity=10_000.0, volume_h24=1.0)

    kept = filter_investable(
        [good, low_liquidity, low_volume], min_liquidity_usd=5_000.0, min_volume_h24_usd=1_000.0
    )

    assert [p.pair_address for p in kept] == ["Pair1"]


class _FakeClient:
    def __init__(self, boosted=None, top_boosted=None, profiles=None, pairs=None):
        self._boosted = boosted or []
        self._top_boosted = top_boosted or []
        self._profiles = profiles or []
        self._pairs = pairs or []
        self.requested_addresses = None

    def get_latest_boosted_tokens(self, chain_id="solana"):
        return self._boosted

    def get_top_boosted_tokens(self, chain_id="solana"):
        return self._top_boosted

    def get_latest_token_profiles(self, chain_id="solana"):
        return self._profiles

    def get_pairs_for_tokens(self, chain_id, addresses):
        self.requested_addresses = addresses
        return self._pairs


def test_discover_candidate_addresses_puts_watchlist_first_and_dedupes():
    client = _FakeClient(boosted=["B1", "Watched"], top_boosted=["T1"], profiles=["P1", "B1"])

    addresses = discover_candidate_addresses(client, watchlist=["Watched", "W2"])

    assert addresses[:2] == ["Watched", "W2"]
    assert addresses.count("B1") == 1
    assert set(addresses) == {"Watched", "W2", "B1", "T1", "P1"}


def test_discover_candidate_addresses_tolerates_feed_failures():
    class BrokenClient(_FakeClient):
        def get_latest_boosted_tokens(self, chain_id="solana"):
            raise RuntimeError("feed down")

    client = BrokenClient(profiles=["P1"])
    addresses = discover_candidate_addresses(client, watchlist=["Watched"])

    assert addresses == ["Watched", "P1"]


def test_discover_pairs_resolves_dedupes_and_filters():
    dust = _pair(address="Dust", pair_address="Dust", liquidity=1.0, volume_h24=1.0)
    low_liq = _pair(address="Token1", pair_address="LowLiq", liquidity=1_000.0)
    high_liq = _pair(address="Token1", pair_address="HighLiq", liquidity=20_000.0)
    client = _FakeClient(boosted=["Token1"], pairs=[dust, low_liq, high_liq])

    pairs = discover_pairs(client, min_liquidity_usd=5_000.0, min_volume_h24_usd=1_000.0)

    assert [p.pair_address for p in pairs] == ["HighLiq"]


def test_discover_pairs_returns_empty_without_resolving_when_no_candidates():
    client = _FakeClient()
    assert discover_pairs(client) == []
    assert client.requested_addresses is None
