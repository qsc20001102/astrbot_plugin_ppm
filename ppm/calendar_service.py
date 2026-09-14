from __future__ import annotations

from datetime import date, timedelta

try:
    from chinese_calendar import is_workday as china_is_workday
except ImportError:  # pragma: no cover - safe fallback when dependency is unavailable
    china_is_workday = None


def default_is_workday(day: date) -> tuple[bool, str]:
    """Return official China workday when supported, otherwise weekday fallback."""
    if china_is_workday is not None:
        try:
            return bool(china_is_workday(day)), "法定日历"
        except NotImplementedError:
            pass
    return day.weekday() < 5, "周末规则"


def month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, next_month - timedelta(days=1)


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
