from trading_agent.alert_sinks import combine, journal_sink
from trading_agent.alerts import Alert


def _alert():
    return Alert(
        event="touch",
        kind="bullish",
        label="fair value gap",
        top=101.0,
        bottom=99.0,
        price=100.0,
        timestamp_ms=1000,
        message="Touched bullish fair value gap [99.00-101.00] at 100.00",
    )


class _RaisingJournal:
    def log_alert(self, alert):
        raise OSError("disk full")


def test_journal_sink_does_not_raise_on_write_failure():
    sink = journal_sink(_RaisingJournal())
    sink(_alert())  # must not raise


def test_combine_runs_every_sink_even_if_one_raises():
    calls = []

    def broken(alert):
        raise RuntimeError("boom")

    def records(alert):
        calls.append(alert.event)

    combine(broken, records)(_alert())

    assert calls == ["touch"]
