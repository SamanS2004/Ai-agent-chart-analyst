import json

from trading_agent.live_stream import parse_ticker_message


def test_parses_ticker_push_message():
    raw = json.dumps(
        {
            "topic": "tickers.BTCUSDT",
            "ts": 1700000000000,
            "type": "snapshot",
            "data": {"symbol": "BTCUSDT", "lastPrice": "60123.45"},
        }
    )
    result = parse_ticker_message(raw)
    assert result == (60123.45, 1700000000000)


def test_ignores_non_ticker_topics():
    raw = json.dumps({"topic": "orderbook.BTCUSDT", "data": {"lastPrice": "1"}})
    assert parse_ticker_message(raw) is None


def test_ignores_subscription_ack():
    raw = json.dumps({"success": True, "op": "subscribe"})
    assert parse_ticker_message(raw) is None


def test_ignores_malformed_json():
    assert parse_ticker_message("not json") is None


def test_ignores_ticker_message_without_price():
    raw = json.dumps({"topic": "tickers.BTCUSDT", "data": {"symbol": "BTCUSDT"}})
    assert parse_ticker_message(raw) is None
