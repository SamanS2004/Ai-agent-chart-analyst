"""Real-time BTC price feed via Bybit's public WebSocket (no auth required).

`parse_ticker_message` is pure and unit-tested without a network connection.
`BybitTickerWebSocket` is a thin, mostly-untested-by-necessity wrapper
around `websocket-client`'s `WebSocketApp` that calls it on every tick.
"""

from __future__ import annotations

import json
import threading
from typing import Callable

import websocket

DEFAULT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

PriceCallback = Callable[[float, int], None]


def parse_ticker_message(raw: str) -> tuple[float, int] | None:
    """Return (last_price, timestamp_ms) from a Bybit `tickers.*` push, or
    None for messages that carry no price (subscription acks, pings, ...)."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not payload.get("topic", "").startswith("tickers."):
        return None

    data = payload.get("data") or {}
    last_price = data.get("lastPrice")
    if last_price is None:
        return None

    timestamp_ms = int(payload.get("ts") or data.get("ts") or 0)
    return float(last_price), timestamp_ms


class BybitTickerWebSocket:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        on_price: PriceCallback | None = None,
        url: str = DEFAULT_WS_URL,
    ) -> None:
        self.symbol = symbol
        self.on_price = on_price
        self.url = url
        self._app: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{self.symbol}"]}))

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        parsed = parse_ticker_message(raw)
        if parsed is not None and self.on_price is not None:
            price, timestamp_ms = parsed
            self.on_price(price, timestamp_ms)

    def start(self) -> None:
        """Connect and stream in a background thread; returns immediately."""
        self._app = websocket.WebSocketApp(
            self.url, on_open=self._on_open, on_message=self._on_message
        )
        self._thread = threading.Thread(
            target=self._app.run_forever,
            kwargs={"ping_interval": 20, "ping_timeout": 10},
            daemon=True,
        )
        self._thread.start()

    def run_forever(self) -> None:
        """Connect and stream, blocking the calling thread until stopped."""
        self._app = websocket.WebSocketApp(
            self.url, on_open=self._on_open, on_message=self._on_message
        )
        self._app.run_forever(ping_interval=20, ping_timeout=10)

    def stop(self) -> None:
        if self._app is not None:
            self._app.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
