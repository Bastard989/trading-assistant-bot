from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_now(timezone_name: str, *, now: datetime | None = None) -> datetime:
    current = now or utc_now()
    if current.tzinfo is None:
        raise ValueError("Business clock requires an aware datetime")
    return current.astimezone(ZoneInfo(timezone_name))


def business_date(timezone_name: str, *, now: datetime | None = None) -> date:
    return business_now(timezone_name, now=now).date()


def utc_day_bounds(day: date, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(day.fromordinal(day.toordinal() + 1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start, end
