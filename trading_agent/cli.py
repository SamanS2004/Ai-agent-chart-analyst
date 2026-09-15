from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from .agent import TradingAgent, format_report
from .alert_sinks import combine, console_sink, desktop_notification_sink, journal_sink, webhook_sink
from .bybit_client import BybitClient
from .journal import TradeJournal
from .live_app import serve as serve_live_app
from .report import load_journal_records, render_dashboard_html
from .tv_webhook import journal_payload_handler, serve as serve_webhook
from .twelvedata_client import TwelveDataClient

_DEFAULT_SYMBOL = {"bybit": "BTCUSDT", "twelvedata": "BTC/USD"}


def _build_client(args: argparse.Namespace):
    symbol = args.symbol or _DEFAULT_SYMBOL[args.data_source]
    if args.data_source == "bybit":
        return BybitClient(symbol=symbol, interval_minutes=args.interval, category=args.category)
    if args.data_source == "twelvedata":
        api_key = args.twelvedata_api_key or os.environ.get("TWELVEDATA_API_KEY")
        if not api_key:
            raise SystemExit(
                "twelvedata data source needs an API key: pass --twelvedata-api-key "
                "or set the TWELVEDATA_API_KEY environment variable "
                "(free key at https://twelvedata.com)"
            )
        return TwelveDataClient(api_key=api_key, symbol=symbol, interval_minutes=args.interval)
    raise SystemExit(f"unknown data source: {args.data_source}")


def _build_agent(args: argparse.Namespace) -> TradingAgent:
    client = _build_client(args)
    journal = TradeJournal(args.journal_dir)
    return TradingAgent(
        client=client,
        journal=journal,
        symbol=client.symbol,
        interval=args.interval,
        candle_limit=args.candle_limit,
    )


def cmd_once(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    result = agent.analyze_once()
    print(format_report(result))
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    print(
        f"Watching {agent.symbol} {agent.interval}m ({args.data_source}) for "
        f"FVG/order-block setups, active 6:00-9:00 America/Los_Angeles. Ctrl+C to stop."
    )
    try:
        agent.run_session(poll_seconds=args.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    sinks = [console_sink, journal_sink(agent.journal)]
    if args.desktop_notify:
        sinks.append(desktop_notification_sink())
    if args.alert_webhook_url:
        sinks.append(webhook_sink(args.alert_webhook_url, secret=args.alert_webhook_secret))
    on_alert = combine(*sinks)

    # Only Bybit has a free public WebSocket here; other sources fall back to
    # REST polling unless the user explicitly asked for ws (and gets a clear
    # error from run_live_alerts if that combination isn't supported).
    price_source = args.price_source or ("ws" if args.data_source == "bybit" else "rest")

    session_note = "always on" if args.always_on else "active 6:00-9:00 America/Los_Angeles"
    print(
        f"Live-monitoring {agent.symbol} {agent.interval}m FVG/order-block zones "
        f"via {price_source} price feed ({session_note}). Ctrl+C to stop."
    )
    try:
        agent.run_live_alerts(
            on_alert=on_alert,
            price_source=price_source,
            recompute_seconds=args.recompute_seconds,
            price_poll_seconds=args.price_poll_seconds,
            always_on=args.always_on,
            proximity_pct=args.proximity_pct,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    records = load_journal_records(Path(args.journal_dir), start=start, end=end)

    symbol = args.symbol
    if symbol is None:
        symbol = next((r["symbol"] for r in reversed(records) if r.get("symbol")), "Chart")
    html_doc = render_dashboard_html(records, symbol=symbol)

    output_path = Path(args.output)
    output_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote dashboard ({len(records)} journal records) to {output_path}")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    price_source = args.price_source or ("ws" if args.data_source == "bybit" else "rest")
    url = f"http://{args.host}:{args.port}"

    def announce_ready() -> None:
        print(
            f"Live chart for {agent.symbol} {agent.interval}m ({args.data_source}, {price_source} feed) "
            f"at {url} -- open it in your browser. Ctrl+C to stop."
        )

    try:
        serve_live_app(
            agent,
            host=args.host,
            port=args.port,
            price_source=price_source,
            recompute_seconds=args.recompute_seconds,
            price_poll_seconds=args.price_poll_seconds,
            proximity_pct=args.proximity_pct,
            on_ready=announce_ready,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(
            f"Could not start the live chart: {exc}\n"
            f"Most likely cause: no network access to your data source "
            f"(--data-source {args.data_source}) from this machine. Check your connection, "
            f"or try --data-source twelvedata with a valid --twelvedata-api-key.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_webhook(args: argparse.Namespace) -> int:
    journal = TradeJournal(args.journal_dir)
    print(f"Listening for TradingView alert webhooks on {args.host}:{args.port}")
    serve_webhook(journal_payload_handler(journal), host=args.host, port=args.port, secret=args.secret)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-agent",
        description=(
            "Fair value gap / order block chart analyst for BTC (TradingView has no "
            "headless public data API). Defaults to Bybit's free public market data; "
            "pass --data-source twelvedata for a non-exchange alternative."
        ),
    )
    parser.add_argument(
        "--data-source",
        choices=["bybit", "twelvedata"],
        default="bybit",
        help="Where to pull chart data from (default: bybit, no API key needed)",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Defaults to BTCUSDT (bybit) or BTC/USD (twelvedata)",
    )
    parser.add_argument("--interval", type=int, default=15, help="Kline interval in minutes")
    parser.add_argument("--category", default="linear", help="Bybit product category (bybit only)")
    parser.add_argument(
        "--twelvedata-api-key",
        default=None,
        help="Or set TWELVEDATA_API_KEY. Free key at https://twelvedata.com (twelvedata only)",
    )
    parser.add_argument("--candle-limit", type=int, default=200)
    parser.add_argument("--journal-dir", default="data/journal")

    subparsers = parser.add_subparsers(dest="command", required=True)

    once = subparsers.add_parser("once", help="Run a single analysis cycle now")
    once.set_defaults(func=cmd_once)

    loop = subparsers.add_parser(
        "loop", help="Run continuously, analyzing every N seconds during the 6-9am PT session"
    )
    loop.add_argument("--poll-seconds", type=int, default=900)
    loop.set_defaults(func=cmd_loop)

    alerts = subparsers.add_parser(
        "alerts",
        help="Live-monitor price against FVG/order-block zones; alert on touch/retest/disrespect",
    )
    alerts.add_argument(
        "--price-source",
        choices=["ws", "rest"],
        default=None,
        help="Defaults to ws for bybit, rest for other sources",
    )
    alerts.add_argument(
        "--recompute-seconds", type=int, default=60, help="How often to refresh zones from klines"
    )
    alerts.add_argument(
        "--price-poll-seconds",
        type=int,
        default=5,
        help="Ticker poll interval when --price-source=rest",
    )
    alerts.add_argument(
        "--always-on",
        action="store_true",
        help="Monitor around the clock instead of only 6-9am Pacific",
    )
    alerts.add_argument(
        "--proximity-pct",
        type=float,
        default=0.05,
        help="Only track zones within this fraction of price (0.05 = 5%%); 0 disables filtering",
    )
    alerts.add_argument("--desktop-notify", action="store_true", help="Best-effort OS notification")
    alerts.add_argument(
        "--alert-webhook-url",
        default=None,
        help="POST each alert as JSON here (e.g. an ntfy.sh topic URL or your own relay)",
    )
    alerts.add_argument("--alert-webhook-secret", default=None)
    alerts.set_defaults(func=cmd_alerts)

    report = subparsers.add_parser(
        "report", help="Render the local journal into a self-contained HTML dashboard"
    )
    report.add_argument("--output", default="data/dashboard.html")
    report.add_argument("--start", default=None, help="YYYY-MM-DD, inclusive")
    report.add_argument("--end", default=None, help="YYYY-MM-DD, inclusive")
    report.set_defaults(func=cmd_report)

    app = subparsers.add_parser(
        "app",
        help="Serve a local live web app: candlestick chart with FVG/order-block zones, updating in real time",
    )
    app.add_argument("--host", default="127.0.0.1")
    app.add_argument("--port", type=int, default=8080)
    app.add_argument(
        "--price-source",
        choices=["ws", "rest"],
        default=None,
        help="Defaults to ws for bybit, rest for other sources",
    )
    app.add_argument(
        "--recompute-seconds", type=int, default=60, help="How often to refresh candles/zones"
    )
    app.add_argument(
        "--price-poll-seconds", type=int, default=3, help="Ticker poll interval when price-source=rest"
    )
    app.add_argument(
        "--proximity-pct",
        type=float,
        default=0.05,
        help="Only draw zones within this fraction of price (0.05 = 5%%); 0 disables filtering",
    )
    app.set_defaults(func=cmd_app)

    webhook = subparsers.add_parser(
        "webhook", help="Start a server to receive TradingView alert webhooks"
    )
    webhook.add_argument("--host", default="0.0.0.0")
    webhook.add_argument("--port", type=int, default=8765)
    webhook.add_argument("--secret", default=None, help="Optional shared secret header check")
    webhook.set_defaults(func=cmd_webhook)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
