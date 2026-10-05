from __future__ import annotations

from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import record_event

from .models import Lesson, LessonType, ScheduleTemplate, Venue

User = get_user_model()


def _ensure_reference_can_be_deactivated(*, lesson_type_id: UUID | None = None, venue_id: UUID | None = None) -> None:
    template_filter = {"is_active": True}
    lesson_filter = {"starts_at__gte": timezone.now()}
    if lesson_type_id is not None:
        template_filter["lesson_type_id"] = lesson_type_id
        lesson_filter["lesson_type_id"] = lesson_type_id
        label = "Тип занятия"
    elif venue_id is not None:
        template_filter["venue_id"] = venue_id
        lesson_filter["venue_id"] = venue_id
        label = "Площадку"
    else:  # pragma: no cover - programming error guard
        raise ValueError("A reference id is required")

    if ScheduleTemplate.objects.filter(**template_filter).exists():
        raise ValidationError(
            f"{label} нельзя деактивировать, пока он используется активным шаблоном расписания. "
            "Сначала завершите или замените шаблон."
        )
    if (
        Lesson.objects.filter(**lesson_filter)
        .exclude(status=Lesson.Status.CANCELLED)
        .exists()
    ):
        raise ValidationError(
            f"{label} нельзя деактивировать, пока он используется будущими неотменёнными занятиями. "
            "Сначала перенесите или отмените эти занятия."
        )


@transaction.atomic
def create_lesson_type(
    *,
    code: str,
    name: str,
    subscription_category: str,
    is_active: bool,
    actor: User | None,
) -> LessonType:
    try:
        lesson_type = LessonType.objects.create(
            code=code,
            name=name,
            subscription_category=subscription_category,
            is_active=is_active,
        )
    except IntegrityError as exc:
        raise ValidationError({"code": "Тип занятия с таким кодом уже существует."}) from exc
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
    actor: User | None,
) -> LessonType:
    lesson_type = LessonType.objects.select_for_update().get(pk=lesson_type_id)
    if lesson_type.is_active and not is_active:
        _ensure_reference_can_be_deactivated(lesson_type_id=lesson_type.id)
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
        lesson_type.save(update_fields=["code", "name", "subscription_category", "is_active"])
    except IntegrityError as exc:
        raise ValidationError({"code": "Тип занятия с таким кодом уже существует."}) from exc
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
    is_active: bool,
    actor: User | None,
) -> Venue:
    try:
        venue = Venue.objects.create(
            code=code,
            name=name,
            address=address,
            is_active=is_active,
        )
    except IntegrityError as exc:
        raise ValidationError({"code": "Площадка с таким кодом уже существует."}) from exc
    record_event(
        event_type="VenueCreated",
        aggregate_type="Venue",
        aggregate_id=venue.id,
        actor=actor,
        payload={
            "code": venue.code,
            "name": venue.name,
            "address": venue.address,
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
    is_active: bool,
    actor: User | None,
) -> Venue:
    venue = Venue.objects.select_for_update().get(pk=venue_id)
    if venue.is_active and not is_active:
        _ensure_reference_can_be_deactivated(venue_id=venue.id)
    previous = {
        "code": venue.code,
        "name": venue.name,
        "address": venue.address,
        "is_active": venue.is_active,
    }
    venue.code = code
    venue.name = name
    venue.address = address
    venue.is_active = is_active
    try:
        venue.save(update_fields=["code", "name", "address", "is_active"])
    except IntegrityError as exc:
        raise ValidationError({"code": "Площадка с таким кодом уже существует."}) from exc
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
                "is_active": venue.is_active,
            },
        },
    )
    return venue
