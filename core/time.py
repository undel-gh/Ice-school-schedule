from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone


def school_timezone() -> ZoneInfo:
    return ZoneInfo(settings.SCHOOL_TIME_ZONE)


def school_date(value: datetime) -> date:
    if timezone.is_aware(value):
        return value.astimezone(school_timezone()).date()
    return value.date()


def make_school_aware(value: datetime) -> datetime:
    if timezone.is_aware(value):
        return value.astimezone(school_timezone())
    return value.replace(tzinfo=school_timezone())


def format_school_datetime(
    value: datetime,
    fmt: str = "%d.%m.%Y %H:%M",
) -> str:
    if timezone.is_aware(value):
        value = value.astimezone(school_timezone())
    else:
        value = make_school_aware(value)
    return value.strftime(fmt)
