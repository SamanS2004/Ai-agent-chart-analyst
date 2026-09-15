import json
from datetime import datetime

from trading_agent.alerts import Alert
from trading_agent.journal import TradeJournal
from trading_agent.models import AnalysisResult


def _read_records(journal_dir, dt):
    path = journal_dir / f"{dt.date().isoformat()}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_log_analysis_tags_type_and_writes_record(tmp_path):
    journal = TradeJournal(tmp_path)
    dt = datetime(2026, 1, 1, 7, 0)
    result = AnalysisResult(symbol="BTCUSDT", interval="15", timestamp_ms=1000, last_price=100.0)

    journal.log_analysis(result, dt=dt)

    records = _read_records(tmp_path, dt)
    assert len(records) == 1
    assert records[0]["type"] == "analysis"
    assert records[0]["last_price"] == 100.0


def test_log_alert_writes_structured_record(tmp_path):
    journal = TradeJournal(tmp_path)
    dt = datetime(2026, 1, 1, 7, 0)
    alert = Alert(
        event="touch",
        kind="bullish",
        label="fair value gap",
        top=101.0,
        bottom=99.0,
        price=100.0,
        timestamp_ms=1000,
        message="Touched bullish fair value gap [99.00-101.00] at 100.00",
    )

    journal.log_alert(alert, dt=dt)

    records = _read_records(tmp_path, dt)
    assert len(records) == 1
    assert records[0]["type"] == "alert"
    assert records[0]["event"] == "touch"
    assert records[0]["message"] == alert.message


def test_log_event_tags_type(tmp_path):
    journal = TradeJournal(tmp_path)
    dt = datetime(2026, 1, 1, 7, 0)

    journal.log_event("outside session window", dt=dt)

    records = _read_records(tmp_path, dt)
    assert records[0]["type"] == "event"
    assert records[0]["event"] == "outside session window"


def test_same_day_records_append_to_one_file(tmp_path):
    journal = TradeJournal(tmp_path)
    dt = datetime(2026, 1, 1, 7, 0)

    journal.log_event("first", dt=dt)
    journal.log_event("second", dt=dt)

    records = _read_records(tmp_path, dt)
    assert [r["event"] for r in records] == ["first", "second"]
