"""Optional receiver for TradingView alert webhooks (stdlib only, no extra deps).

TradingView lets you attach a webhook URL to a Pine Script alert (Pro plans
and above); it will POST the alert's JSON text to that URL. This does not
give programmatic access to historical chart data, but it does let a
TradingView-side alert (e.g. "price entered my FVG box") feed straight into
this agent's journal. Point the alert's webhook URL at this server.
"""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

from .journal import TradeJournal

PayloadHandler = Callable[[dict], None]


def _make_handler(on_payload: PayloadHandler, secret: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (stdlib method name)
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""

            if secret is not None:
                provided = self.headers.get("X-Webhook-Secret", "")
                if not hmac.compare_digest(provided, secret):
                    self.send_response(401)
                    self.end_headers()
                    return

            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw_text": raw.decode("utf-8", errors="replace")}

            on_payload(payload)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # keep stdout to the agent's own reports

    return Handler


def journal_payload_handler(journal: TradeJournal) -> PayloadHandler:
    def handle(payload: dict) -> None:
        journal.log_event(f"TradingView alert received: {json.dumps(payload)}")

    return handle


def serve(
    on_payload: PayloadHandler,
    host: str = "0.0.0.0",
    port: int = 8765,
    secret: str | None = None,
) -> None:
    server = HTTPServer((host, port), _make_handler(on_payload, secret))
    try:
        server.serve_forever()
    finally:
        server.server_close()
