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
