from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any

from .errors import ValidationError


def required_text(payload: dict[str, Any], key: str, max_length: int = 200) -> str:
    value = optional_text(payload, key, max_length)
    if not value:
        raise ValidationError(f"{key} 不能为空")
    if len(value) > max_length:
        raise ValidationError(f"{key} 最多 {max_length} 个字符")
    return value


def optional_text(payload: dict[str, Any], key: str, max_length: int = 2000) -> str:
    value = payload.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{key} 必须是文本")
    value = value.strip()
    if len(value) > max_length:
        raise ValidationError(f"{key} 最多 {max_length} 个字符")
    return value


def parse_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{name} 必须是整数")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        raise ValidationError(f"{name} 必须是整数")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} 必须是整数") from exc


def parse_id(value: Any, name: str = "id") -> int:
    result = parse_integer(value, name)
    if result <= 0:
        raise ValidationError(f"{name} 必须大于 0")
    if result > 9223372036854775807:
        raise ValidationError(f"{name} 超出支持范围")
    return result


def parse_ids(value: Any, name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValidationError(f"{name} 必须是数组")
    return list(dict.fromkeys(parse_id(item, name) for item in value))


def parse_date(value: Any, name: str = "date") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValidationError(f"{name} 必须是 YYYY-MM-DD 格式")
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
