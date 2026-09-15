from __future__ import annotations

import argparse
import sys

from .agent import TradingAgent, format_report
from .bybit_client import BybitClient
from .journal import TradeJournal
from .tv_webhook import journal_payload_handler, serve


def _build_agent(args: argparse.Namespace) -> TradingAgent:
    client = BybitClient()
    journal = TradeJournal(args.journal_dir)
    return TradingAgent(
        client=client,
        journal=journal,
        symbol=args.symbol,
        interval=args.interval,
        category=args.category,
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
        f"Watching {args.symbol} {args.interval}m for FVG/order-block setups, "
        f"active 6:00-9:00 America/Los_Angeles. Ctrl+C to stop."
    )
    try:
        agent.run_session(poll_seconds=args.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_webhook(args: argparse.Namespace) -> int:
    journal = TradeJournal(args.journal_dir)
    print(f"Listening for TradingView alert webhooks on {args.host}:{args.port}")
    serve(journal_payload_handler(journal), host=args.host, port=args.port, secret=args.secret)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-agent",
        description=(
            "Fair value gap / order block chart analyst for BTC, sourced from Bybit "
            "public market data (TradingView has no headless public data API)."
        ),
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15", help="Kline interval in minutes")
    parser.add_argument("--category", default="linear", help="Bybit product category")
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
