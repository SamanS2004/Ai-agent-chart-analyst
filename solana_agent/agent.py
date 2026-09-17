"""Orchestrates discovery (which tokens to watch, on whichever chain is
configured) and polling (price/volume -> local tracking -> signal
evaluation -> alert dispatch) into one real-time watch loop, plus a
single-cycle `once` mode for testing/cron use."""

from __future__ import annotations

import time
from typing import Callable

from .discovery import (
    SOLANA_CHAIN_ID,
    best_pair_per_token,
    discover_candidate_addresses,
    filter_established,
    filter_investable,
)
from .dexscreener_client import DexScreenerClient
from .journal import SolanaJournal
from .models import Alert, TrackedPair
from .signals import SignalEngine
from .tracker import PairTracker


class SolanaMemecoinAgent:
    def __init__(
        self,
        client: DexScreenerClient,
        tracker: PairTracker,
        engine: SignalEngine,
        journal: SolanaJournal,
        watchlist: list[str] | None = None,
        use_boosted: bool = True,
        use_profiles: bool = False,
        chain_id: str = SOLANA_CHAIN_ID,
        min_liquidity_usd: float = 25_000.0,
        min_volume_h24_usd: float = 20_000.0,
        min_pair_age_days: float = 30.0,
    ) -> None:
        self.client = client
        self.tracker = tracker
        self.engine = engine
        self.journal = journal
        self.watchlist = watchlist or []
        self.use_boosted = use_boosted
        self.use_profiles = use_profiles
        self.chain_id = chain_id
        self.min_liquidity_usd = min_liquidity_usd
        self.min_volume_h24_usd = min_volume_h24_usd
        self.min_pair_age_days = min_pair_age_days

    def refresh_candidates(self) -> list[str]:
        return discover_candidate_addresses(
            self.client,
            watchlist=self.watchlist,
            use_boosted=self.use_boosted,
            use_profiles=self.use_profiles,
            chain_id=self.chain_id,
        )

    def poll_and_signal(
        self, addresses: list[str], on_alert: Callable[[Alert], None]
    ) -> list[TrackedPair]:
        if not addresses:
            return []
        pairs = self.client.get_pairs_for_tokens(self.chain_id, addresses)
        pairs = best_pair_per_token(pairs)
        pairs = filter_investable(
            pairs,
            min_liquidity_usd=self.min_liquidity_usd,
            min_volume_h24_usd=self.min_volume_h24_usd,
        )
        pairs = filter_established(pairs, min_age_days=self.min_pair_age_days)

        now_ms = int(time.time() * 1000)
        current_addresses = {p.pair_address for p in pairs}
        # Drop local history for pairs no longer discovered -- keeps memory
        # bounded on a long-running watch instead of accumulating forever.
        for stale in set(self.tracker.known_pair_addresses()) - current_addresses:
            self.tracker.forget(stale)
            self.engine.forget(stale)

        tracked_list = []
        for pair in pairs:
            tracked = self.tracker.update(pair, now_ms)
            tracked_list.append(tracked)
            for alert in self.engine.evaluate(tracked):
                on_alert(alert)
        return tracked_list

    def run_once(self) -> tuple[list[TrackedPair], list[Alert]]:
        addresses = self.refresh_candidates()
        fired: list[Alert] = []
        tracked_list = self.poll_and_signal(addresses, on_alert=fired.append)
        return tracked_list, fired

    def run_watch(
        self,
        on_alert: Callable[[Alert], None],
        poll_seconds: int = 30,
        discover_seconds: int = 300,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_cycles: int | None = None,
    ) -> None:
        """Poll every `poll_seconds`; refresh the candidate token list (the
        trending/boosted feeds, rate-limited lower) every `discover_seconds`."""
        addresses = self.refresh_candidates()
        elapsed_since_discover = 0
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                self.poll_and_signal(addresses, on_alert)
            except Exception as exc:  # keep watching even if one poll fails
                self.journal.log_event(f"poll error: {exc}")

            cycles += 1
            sleep_fn(poll_seconds)
            elapsed_since_discover += poll_seconds

            if elapsed_since_discover >= discover_seconds:
                try:
                    addresses = self.refresh_candidates()
                except Exception as exc:
                    self.journal.log_event(f"discovery error: {exc}")
                elapsed_since_discover = 0


def format_tracked(tracked_list: list[TrackedPair]) -> str:
    if not tracked_list:
        return "no candidate pairs met the liquidity/volume floor this cycle."
    ranked = sorted(tracked_list, key=lambda t: t.gain_pct, reverse=True)
    lines = [f"tracking {len(ranked)} pair(s):"]
    for t in ranked[:20]:
        p = t.pair
        lines.append(
            f"  {p.base_symbol:<12} {t.gain_pct:+6.1f}% gain  {t.volume_multiplier:5.1f}x vol  "
            f"${p.price_usd:.8f}  liq ${p.liquidity_usd:,.0f}  ({p.dex_id})"
        )
    return "\n".join(lines)
