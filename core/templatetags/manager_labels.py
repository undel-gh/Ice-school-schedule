from django import template

from core.presentation import manager_label as get_manager_label

register = template.Library()


@register.filter(name="manager_label")
def manager_label(value, kind):
    return get_manager_label(value, kind)
