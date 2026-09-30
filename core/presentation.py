from __future__ import annotations

from django.core.exceptions import ValidationError


def validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return " ".join(
            message
            for messages_ in exc.message_dict.values()
            for message in messages_
        )
    return " ".join(exc.messages)


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
}


def manager_label(value, kind: str) -> str:
    return MANAGER_LABELS.get(kind, {}).get(str(value), str(value))


def localized_choices(kind: str, choices) -> tuple[tuple[str, str], ...]:
    return tuple(
        (value, manager_label(value, kind))
        for value, _label in choices
    )
