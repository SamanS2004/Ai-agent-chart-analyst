"""Orchestrates one analysis cycle, or a full 6-9am Pacific session loop."""

from __future__ import annotations

import time
from typing import Callable

from .bybit_client import BybitClient
from .journal import TradeJournal
from .models import AnalysisResult
from .session import PACIFIC, is_session_active, seconds_until_next_session
from .smc import find_confluence_zones, find_fair_value_gaps, find_order_blocks
from .strategy import generate_trade_idea


class TradingAgent:
    def __init__(
        self,
        client: BybitClient,
        journal: TradeJournal,
        symbol: str = "BTCUSDT",
        interval: str = "15",
        category: str = "linear",
        candle_limit: int = 200,
    ) -> None:
        self.client = client
        self.journal = journal
        self.symbol = symbol
        self.interval = interval
        self.category = category
        self.candle_limit = candle_limit

    def analyze_once(self) -> AnalysisResult:
        candles = self.client.get_klines(
            symbol=self.symbol,
            interval=self.interval,
            category=self.category,
            limit=self.candle_limit,
        )
        if not candles:
            raise RuntimeError(f"No candles returned for {self.symbol} {self.interval}m")

        fvgs = find_fair_value_gaps(candles)
        order_blocks = find_order_blocks(candles)
        zones = find_confluence_zones(fvgs, order_blocks)
        idea = generate_trade_idea(candles, fvgs, order_blocks, zones)

        last = candles[-1]
        result = AnalysisResult(
            symbol=self.symbol,
            interval=self.interval,
            timestamp_ms=last.timestamp_ms,
            last_price=last.close,
            fair_value_gaps=fvgs,
            order_blocks=order_blocks,
            confluence_zones=zones,
            trade_idea=idea,
        )
        self.journal.log_analysis(result)
        return result

    def run_session(
        self,
        poll_seconds: int = 900,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_cycles: int | None = None,
        tz=PACIFIC,
        report_fn: Callable[[str], None] = print,
    ) -> None:
        """Analyze every `poll_seconds` while it's 6-9am Pacific; idle outside it."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            if is_session_active(tz=tz):
                result = self.analyze_once()
                report_fn(format_report(result))
                cycles += 1
                sleep_fn(poll_seconds)
            else:
                wait = seconds_until_next_session(tz=tz)
                self.journal.log_event(
                    f"Outside the 6-9am Pacific session; next session in {wait:.0f}s."
                )
                sleep_fn(min(wait, poll_seconds))


def format_report(result: AnalysisResult) -> str:
    lines = [
        f"[{result.symbol} {result.interval}m] last price: {result.last_price:.2f}",
        f"  unmitigated FVGs: {sum(not f.mitigated for f in result.fair_value_gaps)}"
        f"  unmitigated order blocks: {sum(not o.mitigated for o in result.order_blocks)}"
        f"  confluence zones: {len(result.confluence_zones)}",
    ]
    if result.trade_idea:
        idea = result.trade_idea
        lines.append(
            f"  IDEA ({idea.confidence}): {idea.kind.upper()} entry={idea.entry} "
            f"stop={idea.stop} targets={idea.targets}"
        )
        lines.append(f"  rationale: {idea.rationale}")
    else:
        lines.append("  no qualifying setup this cycle.")
    return "\n".join(lines)
