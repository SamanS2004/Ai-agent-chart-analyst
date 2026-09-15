from datetime import datetime
from zoneinfo import ZoneInfo

from trading_agent.session import (
    PACIFIC,
    is_session_active,
    seconds_until_next_session,
    seconds_until_session_end,
)


def pt(y, m, d, h, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=PACIFIC)


def test_active_inside_window():
    assert is_session_active(pt(2026, 1, 5, 7, 30)) is True


def test_inactive_before_window():
    assert is_session_active(pt(2026, 1, 5, 5, 59)) is False


def test_inactive_at_end_boundary():
    assert is_session_active(pt(2026, 1, 5, 9, 0)) is False


def test_active_at_start_boundary():
    assert is_session_active(pt(2026, 1, 5, 6, 0)) is True


def test_converts_other_timezones_correctly():
    # 14:30 UTC in January is 06:30 Pacific (PST, UTC-8)
    utc_time = datetime(2026, 1, 5, 14, 30, tzinfo=ZoneInfo("UTC"))
    assert is_session_active(utc_time) is True


def test_seconds_until_next_session_same_day():
    now = pt(2026, 1, 5, 3, 0)
    wait = seconds_until_next_session(now)
    assert wait == 3 * 3600


def test_seconds_until_next_session_rolls_to_tomorrow():
    now = pt(2026, 1, 5, 10, 0)
    wait = seconds_until_next_session(now)
    assert wait == 20 * 3600


def test_seconds_until_session_end():
    now = pt(2026, 1, 5, 7, 0)
    assert seconds_until_session_end(now) == 2 * 3600
