"""6am-9am Pacific trading session window."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def is_session_active(
    now: datetime | None = None,
    tz: ZoneInfo = PACIFIC,
    start_hour: int = 6,
    end_hour: int = 9,
) -> bool:
    now = (now or datetime.now(tz)).astimezone(tz)
    return start_hour <= now.hour < end_hour


def seconds_until_next_session(
    now: datetime | None = None,
    tz: ZoneInfo = PACIFIC,
    start_hour: int = 6,
) -> float:
    now = (now or datetime.now(tz)).astimezone(tz)
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


def seconds_until_session_end(
    now: datetime | None = None,
    tz: ZoneInfo = PACIFIC,
    end_hour: int = 9,
) -> float:
    now = (now or datetime.now(tz)).astimezone(tz)
    candidate = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()
