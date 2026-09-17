import json

from solana_agent.journal import SolanaJournal
from solana_agent.models import Alert


def _alert():
    return Alert(
        event="volume_and_gain",
        pair_address="Pair1",
        symbol="COIN",
        name="Coin",
        dex_id="raydium",
        price_usd=0.001,
        gain_pct=12.5,
        volume_multiplier=3.0,
        volume_usd_recent=500.0,
        liquidity_usd=10_000.0,
        url="https://dexscreener.com/solana/pair1",
        timestamp_ms=1234,
        message="COIN up 12.5%",
    )


def test_log_alert_appends_jsonl_record(tmp_path):
    journal = SolanaJournal(tmp_path)
    path = journal.log_alert(_alert())

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "alert"
    assert record["symbol"] == "COIN"
    assert record["gain_pct"] == 12.5


def test_log_event_appends_jsonl_record(tmp_path):
    journal = SolanaJournal(tmp_path)
    path = journal.log_event("discovery error: boom")

    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["type"] == "event"
    assert record["event"] == "discovery error: boom"


def test_multiple_writes_append_to_same_day_file(tmp_path):
    journal = SolanaJournal(tmp_path)
    journal.log_alert(_alert())
    journal.log_event("hello")

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert len(files[0].read_text(encoding="utf-8").strip().splitlines()) == 2
