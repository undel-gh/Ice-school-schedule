from django import template

from core.time import format_school_datetime

register = template.Library()


@register.filter(name="school_datetime")
def school_datetime(value, fmt="%d.%m.%Y %H:%M"):
    if value is None:
        return ""
    return format_school_datetime(value, fmt)
