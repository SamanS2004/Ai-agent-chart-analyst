"""Orchestrates one analysis cycle, or a full 6-9am Pacific session loop."""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Literal, Protocol

from .alerts import Alert, ZoneAlertEngine, filter_zones_near_price, zone_specs_from_detections
from .bybit_client import BybitClient
from .journal import TradeJournal
from .live_stream import BybitTickerWebSocket
from .models import (
    AnalysisResult,
    Candle,
    ConfluenceZone,
    FairValueGap,
    OrderBlock,
    TradeIdea,
)
from .session import PACIFIC, is_session_active, seconds_until_next_session
from .smc import find_confluence_zones, find_fair_value_gaps, find_order_blocks
from .strategy import generate_trade_idea


class ChartDataClient(Protocol):
    """What TradingAgent needs from a data source -- BybitClient and
    TwelveDataClient both implement this; a source is already configured
    (symbol, interval, ...) at construction, so these calls take no params
    beyond how much history to fetch."""

    def get_klines(self, limit: int = 200) -> list[Candle]: ...
    def get_ticker_price(self) -> float: ...


class TradingAgent:
    def __init__(
        self,
        client: ChartDataClient,
        journal: TradeJournal,
        symbol: str = "BTCUSDT",
        interval: int = 15,
        candle_limit: int = 200,
    ) -> None:
        """`symbol`/`interval` are for labeling journal entries and reports --
        the client is already configured with whatever it needs to fetch."""
        self.client = client
        self.journal = journal
        self.symbol = symbol
        self.interval = interval
        self.candle_limit = candle_limit

    def fetch_and_detect(
        self,
    ) -> tuple[
        list[Candle], list[FairValueGap], list[OrderBlock], list[ConfluenceZone], TradeIdea | None
    ]:
        """Fetch fresh candles and run the full detection pipeline. Shared by
        analyze_once, the alerts zone-recompute loop, and the live web app --
        the single place that turns raw candles into zones/idea."""
        candles = self.client.get_klines(limit=self.candle_limit)
        if not candles:
            raise RuntimeError(f"No candles returned for {self.symbol} {self.interval}m")

        fvgs = find_fair_value_gaps(candles)
        order_blocks = find_order_blocks(candles)
        zones = find_confluence_zones(fvgs, order_blocks)
        idea = generate_trade_idea(candles, fvgs, order_blocks, zones)
        return candles, fvgs, order_blocks, zones, idea

    def analyze_once(self) -> AnalysisResult:
        candles, fvgs, order_blocks, zones, idea = self.fetch_and_detect()

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

    def _refresh_zones(self, engine: ZoneAlertEngine, proximity_pct: float = 0.05) -> None:
        candles, fvgs, order_blocks, _zones, _idea = self.fetch_and_detect()
        specs = zone_specs_from_detections(fvgs, order_blocks)
        specs = filter_zones_near_price(specs, candles[-1].close, proximity_pct)
        engine.sync_zones(specs)

    def run_live_alerts(
        self,
        on_alert: Callable[[Alert], None],
        price_source: Literal["ws", "rest"] = "ws",
        recompute_seconds: int = 60,
        price_poll_seconds: int = 5,
        tz=PACIFIC,
        always_on: bool = False,
        proximity_pct: float = 0.05,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_price_ticks: int | None = None,
    ) -> None:
        """Watch live price against current FVG/order-block zones and fire
        touch/retest/disrespect alerts as they happen. Zones are recomputed
        from fresh candles every `recompute_seconds`; by default active only
        during the 6-9am Pacific session (set `always_on=True` to disable
        that restriction). Only zones within `proximity_pct` of price are
        tracked, so old, far-away history doesn't flood alerts."""
        if price_source == "ws" and not isinstance(self.client, BybitClient):
            raise ValueError(
                "price_source='ws' is only supported with BybitClient (the only "
                "source with a free public WebSocket here) -- use price_source='rest' "
                "for other data sources."
            )
        engine = ZoneAlertEngine()

        def handle_price(price: float, timestamp_ms: int | None = None) -> None:
            if not (always_on or is_session_active(tz=tz)):
                return
            for alert in engine.on_price(price, timestamp_ms):
                on_alert(alert)

        if price_source == "rest":
            self._run_live_alerts_rest(
                engine,
                handle_price,
                recompute_seconds=recompute_seconds,
                price_poll_seconds=price_poll_seconds,
                tz=tz,
                always_on=always_on,
                proximity_pct=proximity_pct,
                sleep_fn=sleep_fn,
                max_price_ticks=max_price_ticks,
            )
            return

        self._refresh_zones(engine, proximity_pct=proximity_pct)
        stop_event = threading.Event()

        def recompute_loop() -> None:
            while not stop_event.wait(recompute_seconds):
                if always_on or is_session_active(tz=tz):
                    try:
                        self._refresh_zones(engine, proximity_pct=proximity_pct)
                    except Exception as exc:  # keep monitoring even if one refresh fails
                        self.journal.log_event(f"zone refresh error: {exc}")

        recompute_thread = threading.Thread(target=recompute_loop, daemon=True)
        recompute_thread.start()

        # Alert delivery (webhook POSTs, subprocess desktop notifications, ...)
        # can each take seconds; running it inline on the WebSocket's message
        # thread risks missing pings and getting disconnected. Hand alerts off
        # to a separate worker thread instead.
        alert_queue: queue.Queue[Alert | None] = queue.Queue()

        def ws_handle_price(price: float, timestamp_ms: int | None = None) -> None:
            if not (always_on or is_session_active(tz=tz)):
                return
            for alert in engine.on_price(price, timestamp_ms):
                alert_queue.put(alert)

        def dispatch_loop() -> None:
            while True:
                alert = alert_queue.get()
                if alert is None:
                    return
                on_alert(alert)

        dispatch_thread = threading.Thread(target=dispatch_loop, daemon=True)
        dispatch_thread.start()

        stream = BybitTickerWebSocket(
            symbol=self.client.symbol,
            on_price=ws_handle_price,
            on_status=lambda msg: self.journal.log_event(f"price stream: {msg}"),
        )
        try:
            stream.run_forever()
        finally:
            stop_event.set()
            recompute_thread.join(timeout=5)
            alert_queue.put(None)
            dispatch_thread.join(timeout=5)

    def _run_live_alerts_rest(
        self,
        engine: ZoneAlertEngine,
        handle_price: Callable[[float, int | None], None],
        recompute_seconds: int,
        price_poll_seconds: int,
        tz,
        always_on: bool,
        proximity_pct: float,
        sleep_fn: Callable[[float], None],
        max_price_ticks: int | None,
    ) -> None:
        elapsed_since_recompute = recompute_seconds  # force an immediate first recompute
        ticks = 0
        while max_price_ticks is None or ticks < max_price_ticks:
            if always_on or is_session_active(tz=tz):
                if elapsed_since_recompute >= recompute_seconds:
                    try:
                        self._refresh_zones(engine, proximity_pct=proximity_pct)
                    except Exception as exc:
                        self.journal.log_event(f"zone refresh error: {exc}")
                    elapsed_since_recompute = 0
                try:
                    price = self.client.get_ticker_price()
                    handle_price(price, None)
                except Exception as exc:
                    self.journal.log_event(f"price poll error: {exc}")
                elapsed_since_recompute += price_poll_seconds
            ticks += 1
            sleep_fn(price_poll_seconds)


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
