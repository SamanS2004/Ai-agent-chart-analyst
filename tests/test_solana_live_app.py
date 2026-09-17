import json
import threading
import time
import urllib.request

import pytest

from solana_agent.agent import SolanaMemecoinAgent
from solana_agent.journal import SolanaJournal
from solana_agent.live_app import (
    AppState,
    Broadcaster,
    _DashboardHTTPServer,
    _Handler,
    run_live_app,
    serve,
)
from solana_agent.models import TokenPair
from solana_agent.signals import SignalEngine, SignalThresholds
from solana_agent.tracker import PairTracker

ONE_YEAR_AGO_MS = int(time.time() * 1000) - 365 * 86_400_000


def _pair(address="Token1", pair_address="Pair1", price=1.0, volume_m5=500.0, price_change_h1=12.0):
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
        pair_created_at_ms=ONE_YEAR_AGO_MS,
        url="https://dexscreener.com/solana/pair1",
    )


class _FakeClient:
    def __init__(self, pairs, raise_on_poll=False):
        self._pairs = pairs
        self.raise_on_poll = raise_on_poll

    def get_latest_boosted_tokens(self, chain_id="solana"):
        return []

    def get_top_boosted_tokens(self, chain_id="solana"):
        return []

    def get_latest_token_profiles(self, chain_id="solana"):
        return []

    def get_pairs_for_tokens(self, chain_id, addresses):
        if self.raise_on_poll:
            raise ConnectionError("no network")
        return self._pairs


def _agent(pairs, tmp_path, raise_on_poll=False):
    client = _FakeClient(pairs, raise_on_poll=raise_on_poll)
    journal = SolanaJournal(tmp_path)
    tracker = PairTracker()
    engine = SignalEngine(SignalThresholds(gain_min_pct=10.0, volume_multiplier=2.0))
    return SolanaMemecoinAgent(
        client=client,
        tracker=tracker,
        engine=engine,
        journal=journal,
        watchlist=["Token1"],
        use_boosted=False,
        use_profiles=False,
        min_liquidity_usd=1_000.0,
        min_volume_h24_usd=100.0,
    )


def test_broadcaster_delivers_to_subscribers():
    b = Broadcaster()
    q1 = b.subscribe()
    q2 = b.subscribe()
    b.publish({"type": "alert", "symbol": "COIN"})

    assert q1.get_nowait() == {"type": "alert", "symbol": "COIN"}
    assert q2.get_nowait() == {"type": "alert", "symbol": "COIN"}


def test_broadcaster_unsubscribe_stops_delivery():
    b = Broadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    b.publish({"type": "alert"})
    assert q.empty()


def test_app_state_update_pairs_and_snapshot():
    state = AppState()
    state.set_thresholds("solana", SignalThresholds())
    tracker = PairTracker()
    tracked = tracker.update(_pair(), now_ms=0)
    state.update_pairs([tracked])

    snap = state.snapshot()
    assert snap["chain_id"] == "solana"
    assert len(snap["pairs"]) == 1
    assert snap["pairs"][0]["symbol"] == "COIN"


def test_app_state_update_pairs_drops_no_longer_tracked():
    state = AppState()
    tracker = PairTracker()
    t1 = tracker.update(_pair(pair_address="Pair1"), now_ms=0)
    state.update_pairs([t1])
    assert len(state.snapshot()["pairs"]) == 1

    state.update_pairs([])  # nothing currently discovered
    assert state.snapshot()["pairs"] == []


def test_app_state_push_alert_caps_at_max_kept():
    from solana_agent.models import Alert

    state = AppState()
    for i in range(60):
        state.push_alert(
            Alert(
                event="volume_and_gain",
                pair_address=f"P{i}",
                symbol="COIN",
                name="Coin",
                dex_id="raydium",
                price_usd=1.0,
                gain_pct=12.0,
                volume_multiplier=3.0,
                volume_usd_recent=500.0,
                liquidity_usd=10_000.0,
                url="https://example.com",
                timestamp_ms=i,
                message="msg",
            )
        )
    assert len(state.snapshot()["alerts"]) == 50
    assert state.snapshot()["alerts"][0]["pair_address"] == "P59"  # newest first


def test_run_live_app_seeds_state_and_publishes_pairs(tmp_path):
    agent = _agent([_pair()], tmp_path)
    state = AppState()
    broadcaster = Broadcaster()
    q = broadcaster.subscribe()
    stop_event = threading.Event()

    run_live_app(agent, broadcaster, state, poll_seconds=9999, discover_seconds=9999, stop_event=stop_event)

    snap = state.snapshot()
    assert len(snap["pairs"]) == 1
    assert snap["pairs"][0]["gain_pct"] == 12.0

    # This fixture also qualifies for an entry alert, so the seed publishes
    # both an "alert" event (fired inline during poll_and_signal) and a
    # "pairs" snapshot -- order between them isn't guaranteed, just presence.
    events = [q.get(timeout=2) for _ in range(2)]
    assert {"pairs", "alert"} == {e["type"] for e in events}
    stop_event.set()


def test_run_live_app_publishes_alert_events(tmp_path):
    agent = _agent([_pair(price_change_h1=12.0, volume_m5=500.0)], tmp_path)
    state = AppState()
    broadcaster = Broadcaster()
    q = broadcaster.subscribe()
    stop_event = threading.Event()

    run_live_app(agent, broadcaster, state, poll_seconds=9999, discover_seconds=9999, stop_event=stop_event)

    events = [q.get(timeout=2) for _ in range(2)]
    assert {"pairs", "alert"} == {e["type"] for e in events}
    alert_event = next(e for e in events if e["type"] == "alert")
    assert alert_event["event"] == "volume_and_gain"
    stop_event.set()


def test_serve_calls_on_ready_only_after_successful_seed(tmp_path, monkeypatch):
    agent = _agent([_pair()], tmp_path)
    monkeypatch.setattr(_DashboardHTTPServer, "serve_forever", lambda self: None)
    monkeypatch.setattr(_DashboardHTTPServer, "server_close", lambda self: None)

    ready_calls = []
    serve(agent, host="127.0.0.1", port=0, poll_seconds=9999, discover_seconds=9999, on_ready=lambda: ready_calls.append(True))

    assert ready_calls == [True]


def test_serve_does_not_call_on_ready_when_initial_poll_fails(tmp_path):
    agent = _agent([_pair()], tmp_path, raise_on_poll=True)

    ready_calls = []
    with pytest.raises(ConnectionError):
        serve(agent, host="127.0.0.1", port=0, on_ready=lambda: ready_calls.append(True))

    assert ready_calls == []


def test_http_server_serves_page_and_state(tmp_path):
    agent = _agent([_pair()], tmp_path)
    state = AppState()
    broadcaster = Broadcaster()
    stop_event = threading.Event()
    run_live_app(agent, broadcaster, state, poll_seconds=9999, discover_seconds=9999, stop_event=stop_event)

    server = _DashboardHTTPServer(("127.0.0.1", 0), _Handler, state, broadcaster)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            assert b"<title>" in resp.read()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=5) as resp:
            body = json.loads(resp.read())
            assert len(body["pairs"]) == 1
            assert body["pairs"][0]["symbol"] == "COIN"
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
