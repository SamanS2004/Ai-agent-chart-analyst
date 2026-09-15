import json
from pathlib import Path

from trading_agent.report import load_journal_records, render_dashboard_html, summarize


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_load_journal_records_reads_and_orders_by_timestamp(tmp_path):
    _write_jsonl(
        tmp_path / "2026-01-02.jsonl",
        [{"type": "analysis", "timestamp_ms": 3000, "last_price": 102, "trade_idea": None}],
    )
    _write_jsonl(
        tmp_path / "2026-01-01.jsonl",
        [{"type": "analysis", "timestamp_ms": 1000, "last_price": 100, "trade_idea": None}],
    )

    records = load_journal_records(tmp_path)

    assert [r["timestamp_ms"] for r in records] == [1000, 3000]


def test_load_journal_records_filters_by_date_range(tmp_path):
    _write_jsonl(tmp_path / "2026-01-01.jsonl", [{"type": "event", "event": "x"}])
    _write_jsonl(tmp_path / "2026-01-05.jsonl", [{"type": "event", "event": "y"}])
    _write_jsonl(tmp_path / "2026-01-10.jsonl", [{"type": "event", "event": "z"}])

    from datetime import date

    records = load_journal_records(tmp_path, start=date(2026, 1, 2), end=date(2026, 1, 9))

    assert [r["event"] for r in records] == ["y"]


def test_load_journal_records_ignores_non_date_files(tmp_path):
    _write_jsonl(tmp_path / "not-a-date.jsonl", [{"type": "event", "event": "x"}])
    assert load_journal_records(tmp_path) == []


def test_summarize_counts_analyses_alerts_and_ideas():
    records = [
        {
            "type": "analysis",
            "timestamp_ms": 1000,
            "last_price": 100.0,
            "trade_idea": {"kind": "long", "confidence": "high"},
        },
        {
            "type": "analysis",
            "timestamp_ms": 2000,
            "last_price": 101.0,
            "trade_idea": None,
        },
        {"type": "alert", "event": "touch", "kind": "bullish"},
        {"type": "alert", "event": "touch", "kind": "bullish"},
        {"type": "alert", "event": "disrespect", "kind": "bearish"},
        {"type": "event", "event": "session note"},
    ]

    s = summarize(records)

    assert s["analysis_count"] == 2
    assert s["alert_count"] == 3
    assert s["alert_breakdown"] == {"touch": 2, "disrespect": 1}
    assert s["idea_count"] == 1
    assert s["idea_breakdown"] == {"long": 1}
    assert s["price_series"] == [(1000, 100.0), (2000, 101.0)]


def test_render_dashboard_html_handles_empty_journal():
    html_doc = render_dashboard_html([], symbol="BTCUSDT")
    assert "<html" in html_doc
    assert "BTCUSDT" in html_doc
    assert "Not enough price history" in html_doc


def test_render_dashboard_html_includes_trade_idea_and_alert_rows():
    records = [
        {
            "type": "analysis",
            "timestamp_ms": 1000,
            "last_price": 100.0,
            "trade_idea": {
                "kind": "long",
                "confidence": "high",
                "entry": 100.0,
                "stop": 98.0,
                "targets": [104.0, 106.0],
                "rationale": "test rationale",
                "zone_type": "fvg",
                "timestamp_ms": 1000,
            },
        },
        {
            "type": "alert",
            "event": "touch",
            "kind": "bullish",
            "label": "fair value gap",
            "top": 101.0,
            "bottom": 99.0,
            "price": 100.0,
            "timestamp_ms": 1200,
            "message": "Touched bullish fair value gap [99.00-101.00] at 100.00",
        },
    ]
    html_doc = render_dashboard_html(records)

    assert "LONG" in html_doc
    assert "test rationale" not in html_doc  # rationale isn't in the table, just entry/stop/targets
    assert "Touched bullish fair value gap" in html_doc
    assert "104.00" in html_doc
