from __future__ import annotations

import argparse
import sys

from .agent import SolanaMemecoinAgent, format_tracked
from .alert_sinks import combine, console_sink, desktop_notification_sink, journal_sink, webhook_sink
from .dexscreener_client import DexScreenerClient
from .journal import SolanaJournal
from .signals import SignalEngine, SignalThresholds
from .tracker import PairTracker


def _parse_addresses(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def _build_agent(args: argparse.Namespace) -> SolanaMemecoinAgent:
    client = DexScreenerClient()
    journal = SolanaJournal(args.journal_dir)
    tracker = PairTracker(lookback_seconds=args.lookback_seconds)
    thresholds = SignalThresholds(
        gain_min_pct=args.gain_min_pct,
        gain_target_max_pct=args.gain_target_max_pct,
        volume_multiplier=args.volume_multiplier,
        reset_buffer_pct=args.reset_buffer_pct,
    )
    engine = SignalEngine(thresholds)
    return SolanaMemecoinAgent(
        client=client,
        tracker=tracker,
        engine=engine,
        journal=journal,
        watchlist=_parse_addresses(args.watch_addresses),
        use_boosted=not args.no_boosted,
        use_profiles=args.include_new_listings,
        chain_id=args.chain_id,
        min_liquidity_usd=args.min_liquidity_usd,
        min_volume_h24_usd=args.min_volume_h24_usd,
        min_pair_age_days=args.min_pair_age_days,
    )


def cmd_once(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    tracked_list, alerts = agent.run_once()
    print(format_tracked(tracked_list))
    for alert in alerts:
        print(f"[ALERT] {alert.message}")
        agent.journal.log_alert(alert)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    agent = _build_agent(args)
    sinks = [console_sink, journal_sink(agent.journal)]
    if args.desktop_notify:
        sinks.append(desktop_notification_sink())
    if args.alert_webhook_url:
        sinks.append(webhook_sink(args.alert_webhook_url, secret=args.alert_webhook_secret))
    on_alert = combine(*sinks)

    print(
        f"Watching Solana memecoins via DexScreener (poll every {args.poll_seconds}s, "
        f"rediscovering trending tokens every {args.discover_seconds}s). "
        f"Alerting on >= {args.gain_min_pct:.0f}% gain with >= {args.volume_multiplier:.1f}x volume. "
        "Ctrl+C to stop."
    )
    try:
        agent.run_watch(
            on_alert=on_alert,
            poll_seconds=args.poll_seconds,
            discover_seconds=args.discover_seconds,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solana-memecoin-agent",
        description=(
            "Tracks well-established Solana memecoins via DexScreener's free public API "
            "(no key needed) and alerts in real time when a token's volume picks up "
            "alongside a 10-15%%+ price gain. Freshly launched tokens are excluded by "
            "default (see --min-pair-age-days) -- this is for coins that already have "
            "real liquidity and volume, not new listings. Not financial advice; "
            "memecoins are extremely high risk (rug pulls, thin liquidity) -- this only "
            "watches and alerts, it never places trades."
        ),
    )
    parser.add_argument(
        "--watch-addresses",
        default=None,
        help="Comma-separated Solana token mint addresses to always track, in addition to "
        "auto-discovered trending tokens",
    )
    parser.add_argument(
        "--no-boosted", action="store_true", help="Don't auto-discover via DexScreener's boosted-token feed"
    )
    parser.add_argument(
        "--include-new-listings",
        action="store_true",
        help="Also auto-discover via DexScreener's newest-submitted-token-profile feed "
        "(off by default: that feed is specifically brand-new listings)",
    )
    parser.add_argument(
        "--min-pair-age-days",
        type=float,
        default=30.0,
        help="Skip any pair younger than this (or with unknown age) -- the main 'stay away "
        "from new pairs' control. 0 disables the age filter",
    )
    parser.add_argument("--chain-id", default="solana")
    parser.add_argument(
        "--min-liquidity-usd",
        type=float,
        default=25_000.0,
        help="Skip pairs with less pool liquidity than this (well-established coins have real liquidity)",
    )
    parser.add_argument(
        "--min-volume-h24-usd",
        type=float,
        default=20_000.0,
        help="Skip pairs with less 24h volume than this (well-established coins have real volume)",
    )
    parser.add_argument(
        "--gain-min-pct", type=float, default=10.0, help="Alert threshold: gain since we started watching (%%)"
    )
    parser.add_argument(
        "--gain-target-max-pct",
        type=float,
        default=15.0,
        help="Upper end of the target gain zone, for alert messaging only (does not cap alerting)",
    )
    parser.add_argument(
        "--volume-multiplier",
        type=float,
        default=2.0,
        help="Alert threshold: recent volume vs. this pair's own baseline rate",
    )
    parser.add_argument(
        "--reset-buffer-pct",
        type=float,
        default=5.0,
        help="Gain must fall this far below --gain-min-pct before a pair can alert again",
    )
    parser.add_argument(
        "--lookback-seconds", type=int, default=3600, help="How much of our own polling history to keep per pair"
    )
    parser.add_argument("--journal-dir", default="data/solana_journal")

    subparsers = parser.add_subparsers(dest="command", required=True)

    once = subparsers.add_parser("once", help="Run a single discover+poll cycle now and print results")
    once.set_defaults(func=cmd_once)

    watch = subparsers.add_parser(
        "watch", help="Continuously watch Solana memecoins and alert in real time"
    )
    watch.add_argument("--poll-seconds", type=int, default=30, help="Price/volume poll interval")
    watch.add_argument(
        "--discover-seconds", type=int, default=300, help="How often to refresh the trending-token list"
    )
    watch.add_argument("--desktop-notify", action="store_true", help="Best-effort OS notification")
    watch.add_argument(
        "--alert-webhook-url",
        default=None,
        help="POST each alert as JSON here (e.g. an ntfy.sh topic URL or your own relay)",
    )
    watch.add_argument("--alert-webhook-secret", default=None)
    watch.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
