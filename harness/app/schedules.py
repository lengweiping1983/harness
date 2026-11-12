"""本地日历调度，时间戳使用 UTC。"""
from __future__ import annotations

from harness.i18n import tr

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.errors import HarnessError


def invalid(message: str) -> HarnessError:
    return HarnessError(message, code="automation/invalid")


def number(value: Any, label: str) -> float:
    message = tr('python.schedules.6689fe82d3b1', p0=f'{label}')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid(message)
    try:
        numeric = float(value)
    except OverflowError:
        raise invalid(message) from None
    if not math.isfinite(numeric):
        raise invalid(message)
    return numeric


def zone(name: str):
    # Windows 不自带 IANA 数据库；这些时区目前没有夏令时。
    fixed = {"UTC": 0, "Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Tokyo": 9}
    if name in fixed:
        return timezone(timedelta(hours=fixed[name]), name)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise invalid(tr('python.schedules.ed19113c71cb')) from None


def _day_list(value: Any, lowest: int, highest: int, label: str, empty: str) -> list[int]:
    """校验一批日历日或月份，去重后按日期顺序排列。"""
    if not isinstance(value, list) or not value:
        raise invalid(tr(empty))
    if any(type(item) is not int or item not in range(lowest, highest + 1) for item in value):
        raise invalid(tr(label))
    return sorted(set(value))


def validate_schedule(body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(body, dict):
        raise invalid(tr('python.schedules.33fa54e90879'))
    name = body.get("timezone", "Asia/Shanghai")
    if not isinstance(name, str):
        raise invalid(tr('python.schedules.aae63ccdbc44'))
    zone(name)
    schedule = body.get("schedule")
    if not isinstance(schedule, dict):
        raise invalid(tr('python.schedules.33fa54e90879'))
    kind = schedule.get("kind")
    if not isinstance(kind, str):
        raise invalid(tr('python.schedules.491ec5b8efe3'))
    if kind == "once":
        at = number(schedule.get("at"), tr('python.schedules.45e03e436ed0'))
        if not 0 < at < 253402214400:
            raise invalid(tr('python.schedules.10af8b8496f7'))
        return {"kind": kind, "at": at}, name
    if kind == "interval":
        seconds = number(schedule.get("seconds"), tr('python.schedules.e9bf2c2feae4'))
        if not 60 <= seconds <= 31536000:
            raise invalid(tr('python.schedules.d6fac8afcc79'))
        return {"kind": kind, "seconds": seconds}, name
    if kind not in {"daily", "weekly", "monthly", "yearly"}:
        raise invalid(tr('python.schedules.78729967be6c'))
    clock = schedule.get("time")
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise invalid(tr('python.schedules.53f81fbad0c4'))
    result = {"kind": kind, "time": clock}
    if kind == "weekly":
        result["days"] = _day_list(schedule.get("days"), 0, 6,
                                   'python.schedules.27be9e12701c', 'python.schedules.27be9e12701c')
    if kind == "monthly":
        result["monthdays"] = _day_list(schedule.get("monthdays"), 1, 31,
                                        'python.schedules.monthdaysRange', 'python.schedules.monthdaysEmpty')
    if kind == "yearly":
        result["months"] = _day_list(schedule.get("months"), 1, 12,
                                     'python.schedules.monthsRange', 'python.schedules.monthsEmpty')
        result["monthdays"] = _day_list(schedule.get("monthdays"), 1, 31,
                                        'python.schedules.monthdaysRange', 'python.schedules.monthdaysEmpty')
    return result, name


def next_due(schedule: dict[str, Any], tz: str, after: float, anchor: float | None = None) -> float:
    kind = schedule["kind"]
    if kind == "once":
        return schedule["at"]
    if kind == "interval":
        seconds = schedule["seconds"]
        if anchor is None:
            return after + seconds
        return anchor + max(1, math.floor((after - anchor) / seconds) + 1) * seconds
    local = datetime.fromtimestamp(after, zone(tz))
    hour, minute = map(int, schedule["time"].split(":"))
    if kind in {"monthly", "yearly"}:
        return _next_calendar_due(local, schedule, hour, minute)
    for offset in range(9):
        day = local.date() + timedelta(days=offset)
        if kind == "weekly" and day.weekday() not in schedule["days"]:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=local.tzinfo)
        stamp = candidate.timestamp()
        # 跳过夏令时不存在的本地时刻，有歧义的时刻只触发一次。
        if datetime.fromtimestamp(stamp, local.tzinfo).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if stamp > after:
            return stamp
    raise invalid(tr('python.schedules.2afde7b11a09'))


def _next_calendar_due(local: datetime, schedule: dict[str, Any], hour: int, minute: int) -> float:
    """按日历月推进每月与每年计划；该月不存在的日期直接跳过。"""
    months = schedule["months"] if schedule["kind"] == "yearly" else list(range(1, 13))
    after = local.timestamp()
    for offset in range(48):
        month = (local.month - 1 + offset) % 12 + 1
        year = local.year + (local.month - 1 + offset) // 12
        if month not in months:
            continue
        for day in schedule["monthdays"]:
            try:
                candidate = datetime(year, month, day, hour, minute, tzinfo=local.tzinfo)
            except ValueError:
                # 当月没有这一天（例如 2 月 31 日），保留到下个月同一天。
                continue
            stamp = candidate.timestamp()
            # 跳过夏令时不存在的本地时刻，有歧义的时刻只触发一次。
            if datetime.fromtimestamp(stamp, local.tzinfo).replace(tzinfo=None) != candidate.replace(tzinfo=None):
                continue
            if stamp > after:
                return stamp
    raise invalid(tr('python.schedules.2afde7b11a09'))
