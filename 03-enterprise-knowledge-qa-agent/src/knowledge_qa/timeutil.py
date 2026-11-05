from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_cst() -> datetime:
    return datetime.now(CST).replace(microsecond=0)
