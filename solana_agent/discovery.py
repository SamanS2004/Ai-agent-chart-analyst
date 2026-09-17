"""Finds candidate Solana memecoin pairs to track: an explicit watchlist
(if given) plus whatever DexScreener's own 'trending' feeds (boosted tokens,
newest profiles) are currently surfacing, resolved to trading pairs and
filtered down to ones actually worth watching (real liquidity, on-chain,
not obvious dust)."""

from __future__ import annotations

from .dexscreener_client import DexScreenerClient
from .models import TokenPair

SOLANA_CHAIN_ID = "solana"


def discover_candidate_addresses(
    client: DexScreenerClient,
    watchlist: list[str] | None = None,
    use_boosted: bool = True,
    use_profiles: bool = True,
    chain_id: str = SOLANA_CHAIN_ID,
) -> list[str]:
    """Token addresses worth resolving to pairs, deduplicated, watchlist first."""
    addresses: list[str] = list(dict.fromkeys(watchlist or []))
    seen = set(addresses)

    def add_all(new: list[str]) -> None:
        for addr in new:
            if addr not in seen:
                seen.add(addr)
                addresses.append(addr)

    if use_boosted:
        try:
            add_all(client.get_latest_boosted_tokens(chain_id=chain_id))
            add_all(client.get_top_boosted_tokens(chain_id=chain_id))
        except Exception:
            pass  # a discovery feed being down shouldn't stop the watchlist half
    if use_profiles:
        try:
            add_all(client.get_latest_token_profiles(chain_id=chain_id))
        except Exception:
            pass
    return addresses


def best_pair_per_token(pairs: list[TokenPair]) -> list[TokenPair]:
    """A token can trade on several pools (Raydium + Orca + a pump.fun curve,
    ...); keep only the highest-liquidity pair per base token as canonical,
    so the same coin isn't tracked/alerted on twice under two pair addresses."""
    best: dict[str, TokenPair] = {}
    for pair in pairs:
        current = best.get(pair.base_token_address)
        if current is None or pair.liquidity_usd > current.liquidity_usd:
            best[pair.base_token_address] = pair
    return list(best.values())


def filter_investable(
    pairs: list[TokenPair],
    min_liquidity_usd: float = 5_000.0,
    min_volume_h24_usd: float = 1_000.0,
) -> list[TokenPair]:
    """Drops dust/dead pairs (near-zero liquidity or volume) that would
    otherwise produce noisy, meaningless "gains" on a $40 pool."""
    return [
        p
        for p in pairs
        if p.liquidity_usd >= min_liquidity_usd and p.volume_h24 >= min_volume_h24_usd
    ]


def discover_pairs(
    client: DexScreenerClient,
    watchlist: list[str] | None = None,
    use_boosted: bool = True,
    use_profiles: bool = True,
    chain_id: str = SOLANA_CHAIN_ID,
    min_liquidity_usd: float = 5_000.0,
    min_volume_h24_usd: float = 1_000.0,
) -> list[TokenPair]:
    addresses = discover_candidate_addresses(
        client,
        watchlist=watchlist,
        use_boosted=use_boosted,
        use_profiles=use_profiles,
        chain_id=chain_id,
    )
    if not addresses:
        return []
    pairs = client.get_pairs_for_tokens(chain_id, addresses)
    pairs = best_pair_per_token(pairs)
    return filter_investable(pairs, min_liquidity_usd=min_liquidity_usd, min_volume_h24_usd=min_volume_h24_usd)
