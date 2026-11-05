"""时间处理：本项目固定使用 Asia/Shanghai（UTC+08:00）业务时区。

不依赖 tzdata/zoneinfo，避免 Windows 精简环境缺少 IANA 数据库时失败。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

CST = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_WINDOW_START = time(9, 0)
DEFAULT_WINDOW_END = time(21, 0)


class TimeValueError(ValueError):
    """时间字段不可解析或不在允许范围。"""


def parse_datetime(value: str | datetime, *, field: str = "时间") -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise TimeValueError(f"{field}不能为空")
        # Python 3.11 的 fromisoformat 支持 +08:00、Z 需要先替换。
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


def date_key(value: str | datetime) -> str:
    return parse_datetime(value).date().isoformat()


def at_local(day: str | date, hour: int, minute: int = 0) -> datetime:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return datetime.combine(day, time(hour, minute), tzinfo=CST)


def parse_hhmm(value: str) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise TimeValueError(f"时间窗口格式应为 HH:MM：{value!r}") from exc


def within_window(
    value: str | datetime,
    start: time = DEFAULT_WINDOW_START,
    end: time = DEFAULT_WINDOW_END,
) -> bool:
    local = parse_datetime(value)
    return start <= local.time().replace(tzinfo=None) <= end


def minutes_between(left: str | datetime, right: str | datetime) -> int:
    return int((parse_datetime(left) - parse_datetime(right)).total_seconds() // 60)


def day_window(
    day: str | date,
    start: time = DEFAULT_WINDOW_START,
    end: time = DEFAULT_WINDOW_END,
    *,
    step_minutes: int = 60,
) -> list[datetime]:
    """返回一天内的整点（默认 60 分钟刻度），含起止时间。"""
    current = at_local(day, start.hour, start.minute)
    stop = at_local(day, end.hour, end.minute)
    points: list[datetime] = []
    while current <= stop:
        points.append(current)
        current += timedelta(minutes=step_minutes)
    return points
