"""Twelve Data REST client -- a non-Bybit alternative chart data source.

Same shape as BybitClient (get_klines / get_ticker_price), so TradingAgent
doesn't need to know which provider it's talking to. Requires your own
Twelve Data API key (free tier at https://twelvedata.com); this is a plain
REST client, independent of any Claude/MCP connector -- it works standalone,
wherever this script runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .models import Candle

DEFAULT_BASE_URL = "https://api.twelvedata.com"

# Twelve Data's intraday intervals; anything not listed here (e.g. odd minute
# counts) isn't offered by their API.
_INTERVAL_MINUTES_TO_CODE = {
    1: "1min",
    5: "5min",
    15: "15min",
    30: "30min",
    45: "45min",
    60: "1h",
    120: "2h",
    240: "4h",
    1440: "1day",
}


class TwelveDataAPIError(RuntimeError):
    pass


class TwelveDataClient:
    def __init__(
        self,
        api_key: str,
        symbol: str = "BTC/USD",
        interval_minutes: int = 15,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                "TwelveDataClient requires an API key -- get a free one at "
                "https://twelvedata.com and pass it or set TWELVEDATA_API_KEY"
            )
        if interval_minutes not in _INTERVAL_MINUTES_TO_CODE:
            supported = ", ".join(str(m) for m in sorted(_INTERVAL_MINUTES_TO_CODE))
            raise ValueError(
                f"Twelve Data doesn't offer a {interval_minutes}-minute interval; "
                f"supported (in minutes): {supported}"
            )
        self.api_key = api_key
        self.symbol = symbol
        self.interval_minutes = interval_minutes
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.timeout = timeout

    @property
    def _interval_code(self) -> str:
        return _INTERVAL_MINUTES_TO_CODE[self.interval_minutes]

    def get_klines(self, limit: int = 200) -> list[Candle]:
        """Fetch candles in chronological (oldest -> newest) order."""
        params = {
            "symbol": self.symbol,
            "interval": self._interval_code,
            "outputsize": min(limit, 5000),
            "apikey": self.api_key,
        }
        response = self._session.get(
            f"{self.base_url}/time_series", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") == "error":
            raise TwelveDataAPIError(payload.get("message", "unknown Twelve Data API error"))

        candles = [
            Candle(
                timestamp_ms=_parse_timestamp_ms(row["datetime"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            )
            for row in payload.get("values", [])
        ]
        candles.reverse()  # Twelve Data returns newest-first
        return candles

    def get_ticker_price(self) -> float:
        """Last traded price, for REST-polling-based live monitoring."""
        params = {"symbol": self.symbol, "apikey": self.api_key}
        response = self._session.get(f"{self.base_url}/price", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        if payload.get("status") == "error":
            raise TwelveDataAPIError(payload.get("message", "unknown Twelve Data API error"))
        if "price" not in payload:
            raise TwelveDataAPIError(f"no price data for {self.symbol}")
        return float(payload["price"])


def _parse_timestamp_ms(datetime_str: str) -> int:
    # Intraday values come as "YYYY-MM-DD HH:MM:SS"; daily+ values as just "YYYY-MM-DD".
    fmt = "%Y-%m-%d %H:%M:%S" if " " in datetime_str else "%Y-%m-%d"
    dt = datetime.strptime(datetime_str, fmt).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
