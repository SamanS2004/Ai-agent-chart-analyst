import json
import threading
import time
import urllib.request

import pytest

from trading_agent.agent import TradingAgent
from trading_agent.journal import TradeJournal
from trading_agent.live_app import (
    AppState,
    Broadcaster,
    _ChartHTTPServer,
    _Handler,
    run_live_app,
    serve,
)
from trading_agent.models import Candle


def candle(ts, o, h, l, cl, v=1.0):
    return Candle(timestamp_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


def _bullish_fvg_candles():
    return [
        candle(0, 100, 101, 99, 100.5),
        candle(1, 100.5, 110, 100, 109),
        candle(2, 109, 115, 108, 114),
    ]


class FakeClient:
    symbol = "BTCUSDT"

    def __init__(self, candles, prices):
        self.candles = candles
        self.prices = list(prices)

    def get_klines(self, **kwargs):
        return self.candles

    def get_ticker_price(self, **kwargs):
        return self.prices.pop(0) if self.prices else self.candles[-1].close


def test_broadcaster_delivers_to_subscribers():
    b = Broadcaster()
    q1 = b.subscribe()
    q2 = b.subscribe()
    b.publish({"type": "price", "price": 100})

    assert q1.get_nowait() == {"type": "price", "price": 100}
    assert q2.get_nowait() == {"type": "price", "price": 100}


def test_broadcaster_unsubscribe_stops_delivery():
    b = Broadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    b.publish({"type": "price", "price": 100})
    assert q.empty()


def test_broadcaster_drops_for_full_queue_without_raising():
    b = Broadcaster()
    q = b.subscribe()
    for i in range(300):  # exceeds the queue's maxsize of 200
        b.publish({"type": "price", "price": i})
    assert q.full()


def test_app_state_snapshot_reflects_analysis():
    from trading_agent.models import AnalysisResult

    state = AppState()
    candles = _bullish_fvg_candles()
    result = AnalysisResult(
        symbol="BTCUSDT", interval=15, timestamp_ms=2, last_price=114.0, trade_idea=None
    )
    state.update_from_analysis(result, candles)

    snap = state.snapshot()
    assert snap["symbol"] == "BTCUSDT"
    assert snap["last_price"] == 114.0
    assert len(snap["candles"]) == 3


def test_app_state_apply_tick_grows_last_candle():
    from trading_agent.models import AnalysisResult

    state = AppState()
    candles = _bullish_fvg_candles()
    result = AnalysisResult(symbol="BTCUSDT", interval=15, timestamp_ms=2, last_price=114.0)
    state.update_from_analysis(result, candles)

    state.apply_tick(120.0)
    snap = state.snapshot()
    assert snap["last_price"] == 120.0
    assert snap["candles"][-1]["close"] == 120.0
    assert snap["candles"][-1]["high"] == 120.0  # new high

    state.apply_tick(90.0)
    snap = state.snapshot()
    assert snap["candles"][-1]["low"] == 90.0  # new low
    assert snap["candles"][-1]["close"] == 90.0


def test_run_live_app_rest_mode_seeds_state_and_publishes(tmp_path):
    client = FakeClient(_bullish_fvg_candles(), prices=[105, 99.5])
    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=client, journal=journal, symbol="BTCUSDT", interval=15)

    state = AppState()
    broadcaster = Broadcaster()
    q = broadcaster.subscribe()
    stop_event = threading.Event()

    run_live_app(
        agent,
        broadcaster,
        state,
        price_source="rest",
        recompute_seconds=9999,
        price_poll_seconds=0.01,
        proximity_pct=0,
        stop_event=stop_event,
    )

    # seeded synchronously before run_live_app returns
    snap = state.snapshot()
    assert snap["symbol"] == "BTCUSDT"
    assert len(snap["candles"]) == 3

    # a zones event was published as part of the initial seed
    first_event = q.get(timeout=2)
    assert first_event["type"] == "zones"

    # background rest_loop should deliver at least one price event
    deadline = time.time() + 2
    price_event = None
    while time.time() < deadline:
        try:
            evt = q.get(timeout=0.2)
        except Exception:
            continue
        if evt["type"] == "price":
            price_event = evt
            break
    stop_event.set()

    assert price_event is not None


def test_serve_calls_on_ready_only_after_successful_seed(tmp_path, monkeypatch):
    # Regression test: the CLI's "open it in your browser" message must not
    # print before the server can actually accept connections, or a startup
    # failure gets masked behind a misleading success message.
    client = FakeClient(_bullish_fvg_candles(), prices=[])
    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=client, journal=journal, symbol="BTCUSDT", interval=15)

    monkeypatch.setattr(_ChartHTTPServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(_ChartHTTPServer, "server_close", lambda self: None)

    ready_calls = []
    serve(
        agent,
        host="127.0.0.1",
        port=0,
        price_source="rest",
        recompute_seconds=9999,
        price_poll_seconds=9999,
        proximity_pct=0,
        on_ready=lambda: ready_calls.append(True),
    )

    assert ready_calls == [True]


def test_serve_does_not_call_on_ready_when_initial_fetch_fails(tmp_path):
    class BrokenClient:
        symbol = "BTCUSDT"

        def get_klines(self, **kwargs):
            raise ConnectionError("no network")

        def get_ticker_price(self, **kwargs):
            raise ConnectionError("no network")

    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=BrokenClient(), journal=journal, symbol="BTCUSDT", interval=15)

    ready_calls = []
    with pytest.raises(ConnectionError):
        serve(
            agent,
            host="127.0.0.1",
            port=0,
            price_source="rest",
            on_ready=lambda: ready_calls.append(True),
        )

    assert ready_calls == []


def test_http_server_serves_state_and_events(tmp_path):
    client = FakeClient(_bullish_fvg_candles(), prices=[])
    journal = TradeJournal(tmp_path)
    agent = TradingAgent(client=client, journal=journal, symbol="BTCUSDT", interval=15)

    state = AppState()
    broadcaster = Broadcaster()
    stop_event = threading.Event()
    run_live_app(
        agent,
        broadcaster,
        state,
        price_source="rest",
        recompute_seconds=9999,
        price_poll_seconds=9999,
        proximity_pct=0,
        stop_event=stop_event,
    )

    server = _ChartHTTPServer(("127.0.0.1", 0), _Handler, state, broadcaster)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            assert b"<title>" in resp.read()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=5) as resp:
            body = json.loads(resp.read())
            assert body["symbol"] == "BTCUSDT"
            assert len(body["candles"]) == 3
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
