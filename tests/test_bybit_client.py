from unittest.mock import MagicMock

import pytest

from trading_agent.bybit_client import BybitAPIError, BybitClient


def _fake_session(payload, status_ok=True):
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = payload
    if status_ok:
        response.raise_for_status.return_value = None
    else:
        response.raise_for_status.side_effect = Exception("http error")
    session.get.return_value = response
    return session


def test_get_klines_parses_and_reorders_chronologically():
    # Bybit returns newest-first rows: [start, open, high, low, close, volume, turnover]
    payload = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                ["3000", "102", "103", "101", "102.5", "10", "1000"],
                ["2000", "101", "102", "100", "101.5", "10", "1000"],
                ["1000", "100", "101", "99", "100.5", "10", "1000"],
            ]
        },
    }
    client = BybitClient(session=_fake_session(payload))
    candles = client.get_klines(symbol="BTCUSDT", interval="15", limit=3)

    assert [c.timestamp_ms for c in candles] == [1000, 2000, 3000]
    assert candles[0].close == 100.5
    assert candles[-1].close == 102.5


def test_get_klines_raises_on_api_error():
    payload = {"retCode": 10001, "retMsg": "invalid symbol", "result": {"list": []}}
    client = BybitClient(session=_fake_session(payload))
    with pytest.raises(BybitAPIError):
        client.get_klines(symbol="NOPE")


def test_get_klines_passes_request_params():
    payload = {"retCode": 0, "retMsg": "OK", "result": {"list": []}}
    session = _fake_session(payload)
    client = BybitClient(session=session)
    client.get_klines(symbol="BTCUSDT", interval="15", category="linear", limit=200)

    _, kwargs = session.get.call_args
    assert kwargs["params"] == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "15",
        "limit": 200,
    }
