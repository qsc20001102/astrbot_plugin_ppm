"""Weekly wall-clock schedules in a fixed, explicitly stored UTC offset."""
from datetime import datetime, timedelta, timezone
import re

from .errors import ValidationError


def weekly_values(payload):
    days = payload.get("push_weekdays", [])
    clock = payload.get("push_time", "")
    offset = payload.get("push_utc_offset", 480)
    if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise ValidationError("周期推送至少选择一个星期，星期值必须为 0～6")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
        raise ValidationError("周期推送时间必须为 HH:MM 格式")
    if type(offset) is not int or not -720 <= offset <= 840:
        raise ValidationError("推送时区必须在 UTC-12:00 到 UTC+14:00 之间")
    return sorted(set(days)), clock, offset


def next_weekly(after: float, days: list[int], clock: str, offset: int) -> float:
    """Return the first selected wall-clock occurrence strictly after `after`."""
    local = datetime.fromtimestamp(after, timezone(timedelta(minutes=offset)))
    hour, minute = map(int, clock.split(":"))
    for distance in range(8):
        candidate = (local + timedelta(days=distance)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in days and candidate.timestamp() > after:
            return candidate.timestamp()
    raise ValueError("无有效的周期推送日期")
