from __future__ import annotations

from django import template
from django.forms import ModelChoiceField

register = template.Library()


_EMPTY_STATE_MESSAGES = {
    ("StudentAccessForm", "user"): (
        "Нет активных пользователей, которым можно выдать доступ. "
        "Сначала создайте или активируйте пользователя либо используйте приглашение."
    ),
    ("CoachProfileForm", "user"): (
        "Нет активных пользователей без профиля тренера. "
        "Сначала создайте или активируйте пользователя либо используйте приглашение тренеру."
    ),
    ("ManagerScheduleTemplateForm", "group"): (
        "Нет активных групп. Сначала создайте или активируйте группу."
    ),
    ("ManagerScheduleTemplateForm", "lesson_type"): (
        "Нет активных типов занятий. Сначала создайте или активируйте тип занятия."
    ),
    ("ManagerScheduleTemplateForm", "coach"): (
        "Нет активных тренеров. Сначала создайте или активируйте тренера."
    ),
    ("ManagerScheduleTemplateForm", "venue"): (
        "Нет активных площадок. Сначала создайте или активируйте площадку."
    ),
    ("ManagerLessonCoachReassignForm", "coach"): (
        "Нет другого активного тренера для замены. "
        "Сначала добавьте или активируйте другого тренера."
    ),
    ("GroupMembershipForm", "student"): (
        "Нет активных учеников. Сначала создайте или активируйте ученика."
    ),
    ("GroupMembershipForm", "group"): (
        "Нет активных групп. Сначала создайте или активируйте группу."
    ),
    ("ManagerSubscriptionIssueForm", "student"): (
        "Нет активных учеников. Сначала создайте или активируйте ученика."
    ),
    ("ManagerSubscriptionIssueForm", "plan"): (
        "Нет тарифов, доступных для выдачи. "
        "Сначала создайте активный тариф и назначьте ему активную модель расчётного периода."
    ),
    ("ManagerPlaceHoldCreateForm", "student"): (
        "Нет активных учеников. Сначала создайте или активируйте ученика."
    ),
    ("ManagerPlaceHoldCreateForm", "group"): (
        "Нет активных групп. Сначала создайте или активируйте группу."
    ),
    ("ManagerPlaceHoldCreateForm", "period_scheme"): (
        "Нет активных моделей расчётного периода. "
        "Сначала создайте или активируйте модель расчётного периода."
    ),
    ("ManagerOneTimeEntitlementForm", "student"): (
        "Нет активных учеников. Сначала создайте или активируйте ученика."
    ),
    ("ManagerOneTimeEntitlementForm", "lesson"): (
        "Нет доступных занятий в окне ±60 дней. "
        "Сначала создайте или сгенерируйте расписание."
    ),
    ("ManagerCompensationCaseCreateForm", "attendance"): (
        "Нет подходящих пропусков. Нужны отметки «Отсутствовал» в окне ±60 дней "
        "без уже открытого или материализованного компенсационного случая."
    ),
    ("ManagerAdministrativeMakeupForm", "source_subscription_allowance"): (
        "Нет подходящих лимитов абонементов. Нужен неотменённый текущий, ожидающий "
        "активации или недавно завершившийся абонемент."
    ),
    ("ManagerAdministrativeMakeupForm", "source_lesson"): (
        "Нет доступных исходных занятий в окне ±60 дней. "
        "Сначала создайте или сгенерируйте расписание."
    ),
    ("ManagerSubscriptionPlanForm", "period_scheme"): (
        "Нет активных моделей расчётного периода. "
        "Сначала создайте или активируйте модель расчётного периода."
    ),
}


def _message_for(bound_field) -> str:
    form = getattr(bound_field, "form", None)
    if form is None:
        return ""
    for form_class in type(form).__mro__:
        message = _EMPTY_STATE_MESSAGES.get(
            (form_class.__name__, bound_field.name)
        )
        if message:
            return message
    return ""


@register.simple_tag
def manager_choice_empty_state(bound_field) -> str:
    """Return actionable copy when a known manager ModelChoiceField has no options."""

    field = getattr(bound_field, "field", None)
    if not isinstance(field, ModelChoiceField):
        return ""
    message = _message_for(bound_field)
    if not message:
        return ""
    queryset = field.queryset
    if queryset is None or queryset.exists():
        return ""
    return message
