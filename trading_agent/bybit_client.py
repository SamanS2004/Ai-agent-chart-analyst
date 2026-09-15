"""Public (no-auth) Bybit v5 market-data client.

TradingView does not expose a public, headless API for pulling historical
OHLC candles from third-party code -- its charting library is an embeddable
UI widget, and real-time data access requires a broker/exchange integration
approval. Bybit's v5 market endpoints are free, require no API key for
market data, and are what this agent uses by default. See tv_webhook.py for
an optional way to accept TradingView *alerts* into the same pipeline.
"""

from __future__ import annotations

import requests

from .models import Candle

DEFAULT_BASE_URL = "https://api.bybit.com"


class BybitAPIError(RuntimeError):
    pass


class BybitClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.timeout = timeout

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "15",
        category: str = "linear",
        limit: int = 200,
    ) -> list[Candle]:
        """Fetch candles in chronological (oldest -> newest) order."""
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        response = self._session.get(
            f"{self.base_url}/v5/market/kline", params=params, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("retCode") != 0:
            raise BybitAPIError(payload.get("retMsg", "unknown Bybit API error"))

        rows = payload["result"]["list"]
        candles = [
            Candle(
                timestamp_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
        candles.reverse()  # Bybit returns newest-first
        return candles
