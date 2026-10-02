from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext


def localize_message(message: str) -> str:
    """Translate a user-visible domain message at the presentation boundary."""
    return gettext(str(message))


def validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return " ".join(
            localize_message(message)
            for messages_ in exc.message_dict.values()
            for message in messages_
        )
    return " ".join(localize_message(message) for message in exc.messages)


MANAGER_LABELS = {
    "lesson_status": {
        "draft": "Черновик",
        "rsvp_open": "Сбор ответов",
        "confirmed": "Подтверждено",
        "completed": "Проведено",
        "closed": "Закрыто",
        "cancelled": "Отменено",
    },
    "lesson_cancellation_reason": {
        "low_attendance": "Недостаточно участников",
        "coach_unavailable": "Тренер недоступен",
        "venue_unavailable": "Площадка недоступна",
        "administrative": "Административная причина",
        "other": "Другая причина",
    },
    "compensation_status": {
        "open": "Открыт",
        "materialized": "Право выдано",
        "reversed": "Отменён",
        "cancelled": "Закрыт",
    },
    "compensation_eligibility": {
        "eligible": "Доступна",
        "limit_exceeded": "Лимит исчерпан",
        "undetermined": "Не определено",
    },
    "absence_reason": {
        "medical": "Медицинская причина",
        "unexcused": "Без уважительной причины",
        "other": "Другая причина",
    },
    "medical_status": {
        "pending": "Ожидает проверки",
        "verified": "Подтверждено",
        "rejected": "Отклонено",
        "revoked": "Отозвано",
    },
    "one_time_entitlement": {
        "single_ice": "Разовое занятие на льду",
        "single_hall": "Разовое занятие в зале",
        "individual_ice": "Индивидуальное занятие на льду",
        "mini_group_ice": "Мини-группа на льду",
        "trial_ice": "Пробное занятие на льду",
    },
    "subscription_category": {
        "ice": "Лёд",
        "hall": "Зал",
    },
    "subscription_period_mode": {
        "calendar_month": "Календарный месяц",
        "rolling_28_first_lesson": "28 дней от первого занятия",
        "fixed_28_days": "Общие 28-дневные периоды",
    },
    "policy_justification": {
        "none": "Не требуется",
        "verified_medical": "Подтверждённая медицинская справка",
    },
    "policy_limit_scope": {
        "student_period": "Ученик + период",
        "category_period": "Ученик + категория + период",
        "lesson_type_period": "Ученик + тип занятия + период",
    },
    "policy_action_type": {
        "free_makeup": "Бесплатная отработка",
        "paid_makeup": "Платная отработка",
        "billing_recalculation": "Перерасчёт оплаты",
    },
    "policy_target_period": {
        "current_period": "Текущий период",
        "next_student_period": "Следующий период ученика",
        "explicit_target_window": "Явное окно",
    },
    "policy_requirement": {
        "none": "Нет дополнительных условий",
        "fee_required": "Требуется оплата",
        "target_subscription_required": "Требуется целевой абонемент",
        "fee_and_target_subscription_required": "Требуются оплата и целевой абонемент",
    },
}


def manager_label(value, kind: str) -> str:
    return MANAGER_LABELS.get(kind, {}).get(str(value), str(value))


def localized_choices(kind: str, choices) -> tuple[tuple[str, str], ...]:
    return tuple(
        (value, manager_label(value, kind))
        for value, _label in choices
    )
