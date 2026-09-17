"""Public (no-auth) DexScreener API client.

DexScreener indexes DEX pools across dozens of chains -- every Solana DEX
(Raydium, Orca, Meteora, pump.fun's bonding-curve pairs once they graduate,
...), Robinhood Chain's Uniswap-family pools, and many more -- under one
free, keyless REST API. No wallet, no RPC node, no paid data provider
needed to watch memecoins on any chain it covers; which chain a given call
hits is just whatever `chain_id` string the caller passes (see
discovery.py's SOLANA_CHAIN_ID / ROBINHOOD_CHAIN_ID).
Docs: https://docs.dexscreener.com/api/reference

Rate limits (documented, per IP): 300 req/min for /latest/dex/*, 60 req/min
for /token-profiles/* and /token-boosts/*. This client makes one request per
call and leaves pacing to the caller (see agent.py's poll intervals).
"""

from __future__ import annotations

from typing import Optional

import requests

from .models import TokenPair

DEFAULT_BASE_URL = "https://api.dexscreener.com"

# DexScreener batches up to 30 token addresses per /latest/dex/tokens/ call.
MAX_TOKENS_PER_REQUEST = 30


class DexScreenerAPIError(RuntimeError):
    pass


class DexScreenerClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[requests.Session] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: Optional[dict] = None) -> object:
        response = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _pairs_from_payload(self, payload: object) -> list[TokenPair]:
        if isinstance(payload, dict):
            raw_pairs = payload.get("pairs") or []
        elif isinstance(payload, list):
            raw_pairs = payload
        else:
            raw_pairs = []
        pairs = []
        for raw in raw_pairs:
            if raw is None:
                continue
            parsed = _parse_pair(raw)
            if parsed is not None:
                pairs.append(parsed)
        return pairs

    def search_pairs(self, query: str) -> list[TokenPair]:
        payload = self._get("/latest/dex/search", params={"q": query})
        return self._pairs_from_payload(payload)

    def get_pairs_for_tokens(self, chain_id: str, token_addresses: list[str]) -> list[TokenPair]:
        """Resolve up to 30 token addresses at a time into their trading pairs.
        Batches transparently if given more than that."""
        all_pairs: list[TokenPair] = []
        for start in range(0, len(token_addresses), MAX_TOKENS_PER_REQUEST):
            batch = token_addresses[start : start + MAX_TOKENS_PER_REQUEST]
            if not batch:
                continue
            payload = self._get(f"/latest/dex/tokens/{','.join(batch)}")
            all_pairs.extend(self._pairs_from_payload(payload))
        return [p for p in all_pairs if p.chain_id == chain_id]

    def get_latest_token_profiles(self, chain_id: Optional[str] = None) -> list[str]:
        """Token addresses from DexScreener's newest submitted token profiles
        (multi-chain feed) -- a common early signal for freshly launched
        memecoins. Returns bare addresses since profiles carry no price data."""
        payload = self._get("/token-profiles/latest/v1")
        return _addresses_from_profile_feed(payload, chain_id)

    def get_latest_boosted_tokens(self, chain_id: Optional[str] = None) -> list[str]:
        """Token addresses whose project just bought a DexScreener boost --
        a decent proxy for 'trending right now', which is exactly the kind
        of token likely to be pumping in volume."""
        payload = self._get("/token-boosts/latest/v1")
        return _addresses_from_profile_feed(payload, chain_id)

    def get_top_boosted_tokens(self, chain_id: Optional[str] = None) -> list[str]:
        payload = self._get("/token-boosts/top/v1")
        return _addresses_from_profile_feed(payload, chain_id)


def _addresses_from_profile_feed(payload: object, chain_id: Optional[str]) -> list[str]:
    items = payload if isinstance(payload, list) else []
    addresses = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if chain_id is not None and item.get("chainId") != chain_id:
            continue
        address = item.get("tokenAddress")
        if address:
            addresses.append(address)
    return addresses


def _parse_pair(raw: dict) -> Optional[TokenPair]:
    try:
        base_token = raw["baseToken"]
        volume = raw.get("volume") or {}
        price_change = raw.get("priceChange") or {}
        liquidity = raw.get("liquidity") or {}
        price_usd = raw.get("priceUsd")
        if price_usd is None:
            return None
        return TokenPair(
            chain_id=raw["chainId"],
            dex_id=raw.get("dexId", ""),
            pair_address=raw["pairAddress"],
            base_token_address=base_token["address"],
            base_symbol=base_token.get("symbol", "?"),
            base_name=base_token.get("name", "?"),
            price_usd=float(price_usd),
            liquidity_usd=float(liquidity.get("usd") or 0.0),
            fdv=float(raw["fdv"]) if raw.get("fdv") is not None else None,
            volume_m5=float(volume.get("m5") or 0.0),
            volume_h1=float(volume.get("h1") or 0.0),
            volume_h6=float(volume.get("h6") or 0.0),
            volume_h24=float(volume.get("h24") or 0.0),
            price_change_m5=float(price_change.get("m5") or 0.0),
            price_change_h1=float(price_change.get("h1") or 0.0),
            price_change_h6=float(price_change.get("h6") or 0.0),
            price_change_h24=float(price_change.get("h24") or 0.0),
            pair_created_at_ms=raw.get("pairCreatedAt"),
            url=raw.get("url", ""),
        )
    except (KeyError, TypeError, ValueError):
        return None
