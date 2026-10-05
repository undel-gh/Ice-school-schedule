from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from audit.services import record_event
from core.permissions import require_permission
from core.time import school_date

from .models import Lesson, LessonType, ScheduleTemplate, Venue

User = get_user_model()


def _reference_usage(
    *,
    now: datetime,
    lesson_type_id: UUID | None = None,
    venue_id: UUID | None = None,
) -> tuple[bool, bool]:
    template_filter = {"is_active": True}
    lesson_filter = {"starts_at__gte": now}
    if lesson_type_id is not None:
        template_filter["lesson_type_id"] = lesson_type_id
        lesson_filter["lesson_type_id"] = lesson_type_id
    elif venue_id is not None:
        template_filter["venue_id"] = venue_id
        lesson_filter["venue_id"] = venue_id
    else:  # pragma: no cover - programming error guard
        raise ValueError("A reference id is required")

    today = school_date(now)
    active_template_exists = (
        ScheduleTemplate.objects.filter(**template_filter)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        .exists()
    )
    future_lesson_exists = (
        Lesson.objects.filter(**lesson_filter)
        .exclude(status=Lesson.Status.CANCELLED)
        .exists()
    )
    return active_template_exists, future_lesson_exists


def _ensure_reference_can_be_deactivated(
    *,
    now: datetime,
    lesson_type_id: UUID | None = None,
    venue_id: UUID | None = None,
) -> None:
    active_template_exists, future_lesson_exists = _reference_usage(
        now=now,
        lesson_type_id=lesson_type_id,
        venue_id=venue_id,
    )
    label = "Тип занятия" if lesson_type_id is not None else "Площадку"
    if active_template_exists:
        raise ValidationError(
            f"{label} нельзя деактивировать, пока он используется активным шаблоном расписания. "
            "Сначала завершите или замените шаблон."
        )
    if future_lesson_exists:
        raise ValidationError(
            f"{label} нельзя деактивировать, пока он используется будущими неотменёнными занятиями. "
            "Сначала перенесите или отмените эти занятия."
        )


def _ensure_lesson_type_category_can_change(
    *,
    lesson_type_id: UUID,
    now: datetime,
) -> None:
    if Lesson.objects.filter(lesson_type_id=lesson_type_id).exists():
        raise ValidationError(
            "Категорию абонемента нельзя изменить у типа занятия, для которого уже есть занятия. "
            "Создайте новый тип занятия с нужной категорией."
        )
    active_template_exists, _ = _reference_usage(
        now=now,
        lesson_type_id=lesson_type_id,
    )
    if active_template_exists:
        raise ValidationError(
            "Категорию абонемента нельзя изменить у типа занятия, который используется "
            "активным шаблоном расписания. Сначала завершите или замените шаблон либо "
            "создайте новый тип занятия с нужной категорией."
        )


@transaction.atomic
def create_lesson_type(
    *,
    code: str,
    name: str,
    subscription_category: str,
    is_active: bool,
    actor: User,
) -> LessonType:
    require_permission(
        actor,
        "scheduling.add_lessontype",
        "Для создания типа занятия требуется соответствующее право.",
    )
    try:
        lesson_type = LessonType.objects.create(
            code=code,
            name=name,
            subscription_category=subscription_category,
            is_active=is_active,
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "Тип занятия с таким кодом уже существует."}
        ) from exc
    record_event(
        event_type="LessonTypeCreated",
        aggregate_type="LessonType",
        aggregate_id=lesson_type.id,
        actor=actor,
        payload={
            "code": lesson_type.code,
            "name": lesson_type.name,
            "subscription_category": lesson_type.subscription_category,
            "is_active": lesson_type.is_active,
        },
    )
    return lesson_type


@transaction.atomic
def update_lesson_type(
    *,
    lesson_type_id: UUID,
    code: str,
    name: str,
    subscription_category: str,
    is_active: bool,
    actor: User,
    now: datetime,
) -> LessonType:
    require_permission(
        actor,
        "scheduling.change_lessontype",
        "Для изменения типа занятия требуется соответствующее право.",
    )
    lesson_type = LessonType.objects.select_for_update().get(pk=lesson_type_id)
    if lesson_type.is_active and not is_active:
        _ensure_reference_can_be_deactivated(
            lesson_type_id=lesson_type.id,
            now=now,
        )
    if lesson_type.subscription_category != subscription_category:
        _ensure_lesson_type_category_can_change(
            lesson_type_id=lesson_type.id,
            now=now,
        )
    previous = {
        "code": lesson_type.code,
        "name": lesson_type.name,
        "subscription_category": lesson_type.subscription_category,
        "is_active": lesson_type.is_active,
    }
    lesson_type.code = code
    lesson_type.name = name
    lesson_type.subscription_category = subscription_category
    lesson_type.is_active = is_active
    try:
        lesson_type.save(
            update_fields=[
                "code",
                "name",
                "subscription_category",
                "is_active",
            ]
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "Тип занятия с таким кодом уже существует."}
        ) from exc
    record_event(
        event_type="LessonTypeChanged",
        aggregate_type="LessonType",
        aggregate_id=lesson_type.id,
        actor=actor,
        payload={
            "previous": previous,
            "current": {
                "code": lesson_type.code,
                "name": lesson_type.name,
                "subscription_category": lesson_type.subscription_category,
                "is_active": lesson_type.is_active,
            },
        },
    )
    return lesson_type


@transaction.atomic
def create_venue(
    *,
    code: str,
    name: str,
    address: str,
    floor: str,
    is_active: bool,
    actor: User,
) -> Venue:
    require_permission(
        actor,
        "scheduling.add_venue",
        "Для создания площадки требуется соответствующее право.",
    )
    try:
        venue = Venue.objects.create(
            code=code,
            name=name,
            address=address,
            floor=floor,
            is_active=is_active,
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "Площадка с таким кодом уже существует."}
        ) from exc
    record_event(
        event_type="VenueCreated",
        aggregate_type="Venue",
        aggregate_id=venue.id,
        actor=actor,
        payload={
            "code": venue.code,
            "name": venue.name,
            "address": venue.address,
            "floor": venue.floor,
            "is_active": venue.is_active,
        },
    )
    return venue


@transaction.atomic
def update_venue(
    *,
    venue_id: UUID,
    code: str,
    name: str,
    address: str,
    floor: str,
    is_active: bool,
    actor: User,
    now: datetime,
) -> Venue:
    require_permission(
        actor,
        "scheduling.change_venue",
        "Для изменения площадки требуется соответствующее право.",
    )
    venue = Venue.objects.select_for_update().get(pk=venue_id)
    if venue.is_active and not is_active:
        _ensure_reference_can_be_deactivated(
            venue_id=venue.id,
            now=now,
        )
    previous = {
        "code": venue.code,
        "name": venue.name,
        "address": venue.address,
        "floor": venue.floor,
        "is_active": venue.is_active,
    }
    venue.code = code
    venue.name = name
    venue.address = address
    venue.floor = floor
    venue.is_active = is_active
    try:
        venue.save(
            update_fields=["code", "name", "address", "floor", "is_active"]
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"code": "Площадка с таким кодом уже существует."}
        ) from exc
    record_event(
        event_type="VenueChanged",
        aggregate_type="Venue",
        aggregate_id=venue.id,
        actor=actor,
        payload={
            "previous": previous,
            "current": {
                "code": venue.code,
                "name": venue.name,
                "address": venue.address,
                "floor": venue.floor,
                "is_active": venue.is_active,
            },
        },
    )
    return venue
