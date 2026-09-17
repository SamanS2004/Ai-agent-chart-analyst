from unittest.mock import MagicMock

from solana_agent.dexscreener_client import DexScreenerClient


def _raw_pair(**overrides):
    base = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "Pair1",
        "baseToken": {"address": "Token1", "symbol": "DOGE2", "name": "Doge Two"},
        "quoteToken": {"address": "So11111111111111111111111111111111111111112", "symbol": "SOL"},
        "priceUsd": "0.000123",
        "liquidity": {"usd": 50000.0},
        "fdv": 1_000_000.0,
        "volume": {"m5": 1000.0, "h1": 6000.0, "h6": 20000.0, "h24": 50000.0},
        "priceChange": {"m5": 1.0, "h1": 12.0, "h6": 20.0, "h24": 30.0},
        "pairCreatedAt": 1690000000000,
        "url": "https://dexscreener.com/solana/pair1",
    }
    base.update(overrides)
    return base


def _fake_session(payload):
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    session.get.return_value = response
    return session


def test_search_pairs_parses_fields():
    session = _fake_session({"pairs": [_raw_pair()]})
    client = DexScreenerClient(session=session)

    pairs = client.search_pairs("doge")

    assert len(pairs) == 1
    p = pairs[0]
    assert p.chain_id == "solana"
    assert p.base_symbol == "DOGE2"
    assert p.price_usd == 0.000123
    assert p.liquidity_usd == 50000.0
    assert p.volume_m5 == 1000.0
    assert p.price_change_h1 == 12.0


def test_search_pairs_skips_pairs_missing_price():
    session = _fake_session({"pairs": [_raw_pair(priceUsd=None), _raw_pair(pairAddress="Pair2")]})
    client = DexScreenerClient(session=session)

    pairs = client.search_pairs("doge")

    assert len(pairs) == 1
    assert pairs[0].pair_address == "Pair2"


def test_get_pairs_for_tokens_filters_by_chain():
    session = _fake_session(
        {"pairs": [_raw_pair(chainId="solana", pairAddress="P1"), _raw_pair(chainId="ethereum", pairAddress="P2")]}
    )
    client = DexScreenerClient(session=session)

    pairs = client.get_pairs_for_tokens("solana", ["Token1"])

    assert [p.pair_address for p in pairs] == ["P1"]


def test_get_pairs_for_tokens_batches_in_groups_of_30():
    session = _fake_session({"pairs": [_raw_pair()]})
    client = DexScreenerClient(session=session)
    addresses = [f"Addr{i}" for i in range(35)]

    client.get_pairs_for_tokens("solana", addresses)

    assert session.get.call_count == 2
    first_url = session.get.call_args_list[0].args[0]
    second_url = session.get.call_args_list[1].args[0]
    assert first_url.count(",") == 29  # 30 addresses
    assert second_url.count(",") == 4  # remaining 5 addresses


def test_latest_boosted_tokens_extracts_and_filters_by_chain():
    session = _fake_session(
        [
            {"chainId": "solana", "tokenAddress": "SolToken1"},
            {"chainId": "ethereum", "tokenAddress": "EthToken1"},
            {"chainId": "solana", "tokenAddress": "SolToken2"},
        ]
    )
    client = DexScreenerClient(session=session)

    addresses = client.get_latest_boosted_tokens(chain_id="solana")

    assert addresses == ["SolToken1", "SolToken2"]


def test_latest_token_profiles_no_chain_filter_returns_all():
    session = _fake_session([{"chainId": "solana", "tokenAddress": "A"}, {"chainId": "base", "tokenAddress": "B"}])
    client = DexScreenerClient(session=session)

    addresses = client.get_latest_token_profiles(chain_id=None)

    assert addresses == ["A", "B"]
