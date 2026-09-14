from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .errors import ValidationError


def required_text(payload: dict[str, Any], key: str, max_length: int = 200) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValidationError(f"{key} 不能为空")
    if len(value) > max_length:
        raise ValidationError(f"{key} 最多 {max_length} 个字符")
    return value


def optional_text(payload: dict[str, Any], key: str, max_length: int = 2000) -> str:
    value = str(payload.get(key, "")).strip()
    if len(value) > max_length:
        raise ValidationError(f"{key} 最多 {max_length} 个字符")
    return value


def parse_id(value: Any, name: str = "id") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} 必须是整数") from exc
    if result <= 0:
        raise ValidationError(f"{name} 必须大于 0")
    return result


def parse_date(value: Any, name: str = "date") -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} 必须是 YYYY-MM-DD 格式") from exc


def parse_datetime(value: Any, name: str = "datetime") -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.isoformat(timespec="seconds")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} 必须是有效的日期时间") from exc
