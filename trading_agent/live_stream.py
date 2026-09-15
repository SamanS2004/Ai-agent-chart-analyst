"""Real-time BTC price feed via Bybit's public WebSocket (no auth required).

`parse_ticker_message` is pure and unit-tested without a network connection.
`BybitTickerWebSocket` is a thin, mostly-untested-by-necessity wrapper
around `websocket-client`'s `WebSocketApp` that calls it on every tick.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

import websocket

DEFAULT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_MAX_BACKOFF_SECONDS = 60.0

PriceCallback = Callable[[float, int], None]
StatusCallback = Callable[[str], None]


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
        on_status: StatusCallback | None = None,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        self.symbol = symbol
        self.on_price = on_price
        self.url = url
        self.on_status = on_status or (lambda _msg: None)
        self.max_backoff_seconds = max_backoff_seconds
        self._app: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._backoff = 1.0

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._backoff = 1.0  # reconnect succeeded -- don't keep growing the delay
        ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{self.symbol}"]}))

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        parsed = parse_ticker_message(raw)
        if parsed is not None and self.on_price is not None:
            price, timestamp_ms = parsed
            self.on_price(price, timestamp_ms)

    def start(self) -> None:
        """Connect and stream (with auto-reconnect) in a background thread;
        returns immediately."""
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def run_forever(self) -> None:
        """Connect and stream, blocking the calling thread until stop() is
        called. Reconnects with exponential backoff on any drop -- a network
        blip or server-side ping timeout must not silently end monitoring."""
        while not self._stop_event.is_set():
            self._app = websocket.WebSocketApp(
                self.url, on_open=self._on_open, on_message=self._on_message
            )
            self._app.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop_event.is_set():
                return
            self.on_status(f"disconnected, reconnecting in {self._backoff:.0f}s")
            time.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, self.max_backoff_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._app is not None:
            self._app.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
