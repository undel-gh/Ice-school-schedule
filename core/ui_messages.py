from __future__ import annotations

import re
from datetime import date, datetime
from uuid import UUID

from .time import format_school_datetime
from .ui_messages_accounts import ACCOUNT_UI_MESSAGES
from .ui_messages_attendance import ATTENDANCE_UI_MESSAGES
from .ui_messages_financial import FINANCIAL_UI_MESSAGES
from .ui_messages_scheduling import SCHEDULING_UI_MESSAGES
from .ui_messages_subscriptions import (
    SUBSCRIPTION_DYNAMIC_PATTERNS,
    SUBSCRIPTION_UI_MESSAGES,
)

UI_MESSAGES = {
    **ACCOUNT_UI_MESSAGES,
    **ATTENDANCE_UI_MESSAGES,
    **FINANCIAL_UI_MESSAGES,
    **SCHEDULING_UI_MESSAGES,
    **SUBSCRIPTION_UI_MESSAGES,
}

_PERMISSION_RE = re.compile(r"^.+ permission is required\.$")

_ATTENDANCE_STATUS_LABELS = {
    "present": "Присутствовал",
    "absent": "Отсутствовал",
}

_LESSON_STATUS_LABELS = {
    "draft": "Черновик",
    "rsvp_open": "Сбор ответов",
    "confirmed": "Подтверждено",
    "completed": "Проведено",
    "closed": "Закрыто",
    "cancelled": "Отменено",
}

_ABSENCE_REASON_LABELS = {
    "medical": "медицинской причины",
    "unexcused": "отсутствия без уважительной причины",
    "other": "другой причины отсутствия",
}


def format_ui_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def format_ui_datetime(value: str) -> str:
    try:
        return format_school_datetime(datetime.fromisoformat(value))
    except ValueError:
        return value


def _label(mapping: dict[str, str], value: str) -> str:
    return mapping.get(value, value)


def _unquote_code(value: str) -> str:
    return value.strip("'\"")


def _lesson_start_label(value: str) -> str | None:
    """Resolve a technical lesson UUID to a user-facing school-local start."""
    try:
        lesson_id = UUID(value)
    except (TypeError, ValueError):
        return None

    # Keep the lookup at the presentation boundary: service messages retain
    # stable technical identifiers while the web UI shows useful context.
    from scheduling.models import Lesson

    starts_at = (
        Lesson.objects.filter(pk=lesson_id)
        .values_list("starts_at", flat=True)
        .first()
    )
    if starts_at is None:
        return None
    return format_school_datetime(starts_at)


def _lesson_nominative(value: str) -> str:
    starts_at = _lesson_start_label(value)
    if starts_at is None:
        return "занятие"
    return f"занятие {starts_at}"


def _lesson_instrumental(value: str) -> str:
    starts_at = _lesson_start_label(value)
    if starts_at is None:
        return "другим занятием"
    return f"занятием {starts_at}"


_DYNAMIC_PATTERNS = (
    # More user-friendly presentation overrides for the generic subscription
    # formatters. Keep the original patterns as a fallback below so this layer
    # remains backward-compatible with technical service messages.
    (
        re.compile(r"^Unsupported allowance categories: (?P<categories>.+)\.$"),
        lambda m: "Переданы неподдерживаемые категории лимитов.",
    ),
    (
        re.compile(r"^Multiple active absence compensation policies match (?P<reason>.+) on (?P<date>\d{4}-\d{2}-\d{2})\.$"),
        lambda m: (
            f"На {format_ui_date(m.group('date'))} найдено несколько активных "
            "политик компенсаций для "
            f"{_label(_ABSENCE_REASON_LABELS, _unquote_code(m.group('reason')))}."
        ),
    ),
    (
        re.compile(r"^Multiple absence compensation windows with the same priority match action (?P<action>[0-9a-f-]+) on (?P<date>\d{4}-\d{2}-\d{2})\.$"),
        lambda m: (
            f"На {format_ui_date(m.group('date'))} для одного действия подходят "
            "несколько окон компенсации с одинаковым приоритетом."
        ),
    ),
    (
        re.compile(r"^Multiple subscriptions match the next student period starting/issued on (?P<date>\d{4}-\d{2}-\d{2})\. Resolve the duplicate/overlap before issuing compensation\.$"),
        lambda m: (
            f"На {format_ui_date(m.group('date'))} найдено несколько абонементов, "
            "подходящих как следующий период ученика. Устраните дубликат или "
            "пересечение до оформления компенсации."
        ),
    ),
    *SUBSCRIPTION_DYNAMIC_PATTERNS,
    (
        re.compile(r'^External identity provider rejected the request \((?P<code>.+)\)\.$'),
        lambda m: f"Внешний провайдер входа отклонил запрос ({m.group('code')}).",
    ),
    (
        re.compile(r'^Cannot reactivate student: group "(?P<group>.+)" has no free place for this membership\. End or move the membership first\.$'),
        lambda m: f"Нельзя повторно активировать ученика: в группе «{m.group('group')}» нет свободного места для этого периода участия. Сначала завершите или перенесите участие.",
    ),
    (
        re.compile(r'^Group capacity of (?P<capacity>\d+) would be exceeded on (?P<date>\d{4}-\d{2}-\d{2})\.$'),
        lambda m: f"На {format_ui_date(m.group('date'))} будет превышена вместимость группы ({m.group('capacity')}).",
    ),
    (
        re.compile(r'^Capacity cannot be lower than existing current or future seat claims \(first conflict: (?P<date>\d{4}-\d{2}-\d{2})\)\.$'),
        lambda m: f"Вместимость нельзя уменьшить ниже уже занятых текущих или будущих мест. Первый конфликт: {format_ui_date(m.group('date'))}.",
    ),
    (
        re.compile(r'^Template versioning would affect a published or processed lesson\. Reschedule/cancel that lesson explicitly first: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: (
            "Новая версия шаблона затронет уже опубликованное или обработанное "
            f"занятие. Сначала явно перенесите или отмените {_lesson_nominative(m.group('id'))}."
        ),
    ),
    (
        re.compile(r'^Template versioning would cancel a DRAFT lesson with an active enrollment or one-time entitlement\. Use the reschedule_lesson command to move that booked lesson to an explicit exception slot outside the new recurring template slot first: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: (
            "Новая версия шаблона отменит занятие-черновик с активной записью или "
            f"разовым правом. Сначала перенесите {_lesson_nominative(m.group('id'))} "
            "в отдельный слот-исключение."
        ),
    ),
    (
        re.compile(r'^Template occurrence is already materialized as lesson (?P<id>[0-9a-f-]+) with status (?P<status>[a-z_]+)\.$'),
        lambda m: (
            f"Регулярное {_lesson_nominative(m.group('id'))} уже создано со статусом "
            f"«{_label(_LESSON_STATUS_LABELS, m.group('status'))}»."
        ),
    ),
    (
        re.compile(r'^The selected coach has another non-cancelled lesson overlapping this time: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: (
            f"У выбранного тренера уже есть {_lesson_nominative(m.group('id'))}, "
            "пересекающееся по времени."
        ),
    ),
    (
        re.compile(r'^Replacement interval overlaps another non-cancelled lesson of this group: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: (
            f"Новое время пересекается с {_lesson_instrumental(m.group('id'))} этой группы."
        ),
    ),
    (
        re.compile(
            r'^Replacement interval overlaps unmaterialized active schedule template '
            r'occurrence "(?P<lesson_type>.+)" at (?P<starts_at>.+)\. '
            r'Skip the conflicting template occurrence first or choose another time\.$'
        ),
        lambda m: (
            "Новое время пересекается с регулярным занятием "
            f"«{m.group('lesson_type')}» {format_ui_datetime(m.group('starts_at'))}. "
            "Явно пропустите конфликтующее занятие шаблона или выберите другое время."
        ),
    ),
    (
        re.compile(r'^Unsupported attendance transition: (?P<old>[a-z_]+) -> (?P<new>[a-z_]+)\.$'),
        lambda m: (
            "Недопустимое изменение посещаемости: "
            f"«{_label(_ATTENDANCE_STATUS_LABELS, m.group('old'))}» → "
            f"«{_label(_ATTENDANCE_STATUS_LABELS, m.group('new'))}»."
        ),
    ),
    (
        re.compile(r'^(?P<count>\d+) active roster participant\(s\) remain unmarked\.$'),
        lambda m: f"Остались неотмеченные участники занятия: {m.group('count')}.",
    ),
    (
        re.compile(r'^Medical make-up expiry must be on or after (?P<date>\d{4}-\d{2}-\d{2})\.$'),
        lambda m: f"Срок медицинской отработки должен заканчиваться не раньше {format_ui_date(m.group('date'))}.",
    ),
)


def localize_domain_message(message: str) -> str:
    translated = UI_MESSAGES.get(message)
    if translated is not None:
        return translated
    if _PERMISSION_RE.match(message):
        return "Недостаточно прав для выполнения этой операции."
    for pattern, formatter in _DYNAMIC_PATTERNS:
        match = pattern.match(message)
        if match:
            return formatter(match)
    return message
