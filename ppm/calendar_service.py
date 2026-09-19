from __future__ import annotations

from datetime import date, timedelta
import calendar
import re

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
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("month 必须是 YYYY-MM 格式")
    start = date.fromisoformat(f"{month}-01")
    return start, start.replace(day=calendar.monthrange(start.year, start.month)[1])


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        if current == end:
            break
        current += timedelta(days=1)
