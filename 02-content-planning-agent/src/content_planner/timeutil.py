"""业务时区与时间计算。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


class TimeValueError(ValueError):
    pass


def parse_datetime(value: str | datetime, *, field: str = "时间") -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise TimeValueError(f"{field}不能为空")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TimeValueError(f"{field}不是合法 ISO 8601 时间：{value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).replace(microsecond=0)


def format_datetime(value: str | datetime) -> str:
    return parse_datetime(value).isoformat(timespec="seconds")


def now_cst() -> datetime:
    return datetime.now(CST).replace(microsecond=0)


def add_minutes(value: str | datetime, minutes: int) -> datetime:
    return parse_datetime(value) + timedelta(minutes=int(minutes))


def minutes_between(later: str | datetime, earlier: str | datetime) -> int:
    return int((parse_datetime(later) - parse_datetime(earlier)).total_seconds() // 60)
