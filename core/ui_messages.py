from __future__ import annotations

import re

from .ui_messages_accounts import ACCOUNT_UI_MESSAGES
from .ui_messages_attendance import ATTENDANCE_UI_MESSAGES
from .ui_messages_scheduling import SCHEDULING_UI_MESSAGES

UI_MESSAGES = {
    **ACCOUNT_UI_MESSAGES,
    **ATTENDANCE_UI_MESSAGES,
    **SCHEDULING_UI_MESSAGES,
}

_PERMISSION_RE = re.compile(r"^.+ permission is required\.$")

_DYNAMIC_PATTERNS = (
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
        lambda m: f"На {m.group('date')} будет превышена вместимость группы ({m.group('capacity')}).",
    ),
    (
        re.compile(r'^Capacity cannot be lower than existing current or future seat claims \(first conflict: (?P<date>\d{4}-\d{2}-\d{2})\)\.$'),
        lambda m: f"Вместимость нельзя уменьшить ниже уже занятых текущих или будущих мест. Первый конфликт: {m.group('date')}.",
    ),
    (
        re.compile(r'^Template versioning would affect a published or processed lesson\. Reschedule/cancel that lesson explicitly first: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: f"Новая версия шаблона затронет уже опубликованное или обработанное занятие. Сначала явно перенесите или отмените занятие {m.group('id')}.",
    ),
    (
        re.compile(r'^Template versioning would cancel a DRAFT lesson with an active enrollment or one-time entitlement\. Use the reschedule_lesson command to move that booked lesson to an explicit exception slot outside the new recurring template slot first: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: f"Новая версия шаблона отменит занятие-черновик с активной записью или разовым правом. Сначала перенесите занятие {m.group('id')} в отдельный слот-исключение.",
    ),
    (
        re.compile(r'^Template occurrence is already materialized as lesson (?P<id>[0-9a-f-]+) with status (?P<status>[a-z_]+)\.$'),
        lambda m: f"Регулярное занятие уже создано как занятие {m.group('id')} со статусом {m.group('status')}.",
    ),
    (
        re.compile(r'^The selected coach has another non-cancelled lesson overlapping this time: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: f"У выбранного тренера есть другое неотменённое занятие, пересекающееся по времени: {m.group('id')}.",
    ),
    (
        re.compile(r'^Replacement interval overlaps another non-cancelled lesson of this group: (?P<id>[0-9a-f-]+)\.$'),
        lambda m: f"Новое время пересекается с другим неотменённым занятием этой группы: {m.group('id')}.",
    ),
    (
        re.compile(r'^Unsupported attendance transition: (?P<old>[a-z_]+) -> (?P<new>[a-z_]+)\.$'),
        lambda m: f"Недопустимое изменение посещаемости: {m.group('old')} → {m.group('new')}.",
    ),
    (
        re.compile(r'^(?P<count>\d+) active roster participant\(s\) remain unmarked\.$'),
        lambda m: f"Остались неотмеченные участники занятия: {m.group('count')}.",
    ),
    (
        re.compile(r'^Medical make-up expiry must be on or after (?P<date>\d{4}-\d{2}-\d{2})\.$'),
        lambda m: f"Срок медицинской отработки должен заканчиваться не раньше {m.group('date')}.",
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
