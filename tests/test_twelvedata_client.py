from unittest.mock import MagicMock

import pytest

from trading_agent.twelvedata_client import TwelveDataAPIError, TwelveDataClient


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


def test_requires_an_api_key():
    with pytest.raises(ValueError):
        TwelveDataClient(api_key="")


def test_rejects_unsupported_interval():
    with pytest.raises(ValueError):
        TwelveDataClient(api_key="k", interval_minutes=7)


def test_get_klines_parses_and_reorders_chronologically():
    # Twelve Data returns newest-first values, like Bybit.
    payload = {
        "status": "ok",
        "values": [
            {
                "datetime": "2026-09-15 12:15:00",
                "open": "102",
                "high": "103",
                "low": "101",
                "close": "102.5",
                "volume": "10",
            },
            {
                "datetime": "2026-09-15 12:00:00",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
            },
        ],
    }
    client = TwelveDataClient(api_key="k", symbol="BTC/USD", session=_fake_session(payload))
    candles = client.get_klines(limit=2)

    assert len(candles) == 2
    assert candles[0].close == 100.5
    assert candles[-1].close == 102.5
    assert candles[0].timestamp_ms < candles[1].timestamp_ms


def test_get_klines_sends_mapped_interval_and_key():
    payload = {"status": "ok", "values": []}
    session = _fake_session(payload)
    client = TwelveDataClient(
        api_key="mykey", symbol="BTC/USD", interval_minutes=15, session=session
    )
    client.get_klines(limit=200)

    _, kwargs = session.get.call_args
    assert kwargs["params"] == {
        "symbol": "BTC/USD",
        "interval": "15min",
        "outputsize": 200,
        "apikey": "mykey",
    }


def test_get_klines_raises_on_api_error():
    payload = {"status": "error", "message": "invalid symbol"}
    client = TwelveDataClient(api_key="k", session=_fake_session(payload))
    with pytest.raises(TwelveDataAPIError):
        client.get_klines()


def test_get_ticker_price_parses_price():
    payload = {"price": "61234.56"}
    client = TwelveDataClient(api_key="k", session=_fake_session(payload))
    assert client.get_ticker_price() == 61234.56


def test_get_ticker_price_raises_on_api_error():
    payload = {"status": "error", "message": "invalid symbol"}
    client = TwelveDataClient(api_key="k", session=_fake_session(payload))
    with pytest.raises(TwelveDataAPIError):
        client.get_ticker_price()


def test_daily_interval_parses_date_only_datetime():
    payload = {
        "status": "ok",
        "values": [{"datetime": "2026-09-15", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}],
    }
    client = TwelveDataClient(api_key="k", interval_minutes=1440, session=_fake_session(payload))
    candles = client.get_klines()
    assert len(candles) == 1
