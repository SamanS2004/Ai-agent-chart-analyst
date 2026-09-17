import pytest

from solana_agent.cli import build_parser


def test_chain_id_defaults_to_solana():
    parser = build_parser()
    args = parser.parse_args(["once"])
    assert args.chain_id == "solana"


def test_chain_id_accepts_robinhood():
    parser = build_parser()
    args = parser.parse_args(["--chain-id", "robinhood", "once"])
    assert args.chain_id == "robinhood"


def test_chain_id_rejects_unsupported_chain():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--chain-id", "ethereum", "once"])


def test_journal_dir_defaults_to_memecoin_journal():
    parser = build_parser()
    args = parser.parse_args(["once"])
    assert args.journal_dir == "data/memecoin_journal"


def test_include_new_listings_defaults_to_false():
    parser = build_parser()
    args = parser.parse_args(["once"])
    assert args.include_new_listings is False


def test_min_pair_age_days_defaults_to_30():
    parser = build_parser()
    args = parser.parse_args(["once"])
    assert args.min_pair_age_days == 30.0


def test_exit_drawdown_pct_defaults_to_8():
    parser = build_parser()
    args = parser.parse_args(["once"])
    assert args.exit_drawdown_pct == 8.0


def test_app_subcommand_defaults():
    parser = build_parser()
    args = parser.parse_args(["app"])
    assert args.command == "app"
    assert args.host == "127.0.0.1"
    assert args.port == 8090
    assert args.poll_seconds == 30
    assert args.discover_seconds == 300


def test_app_subcommand_accepts_custom_host_and_port():
    parser = build_parser()
    args = parser.parse_args(["app", "--host", "0.0.0.0", "--port", "9090"])
    assert args.host == "0.0.0.0"
    assert args.port == 9090
