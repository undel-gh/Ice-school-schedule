from __future__ import annotations

from datetime import datetime

from core.time import make_school_aware


def school_dt(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    """Build an aware datetime from a school's local wall-clock value."""
    return make_school_aware(datetime(year, month, day, hour, minute))
