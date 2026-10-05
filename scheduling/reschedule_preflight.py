from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from core.permissions import require_permission
from core.time import make_school_aware, school_date

from .models import Lesson, ScheduleTemplate, TrainingGroup


_RESCHEDULABLE_STATUSES = {
    Lesson.Status.DRAFT,
    Lesson.Status.RSVP_OPEN,
    Lesson.Status.CONFIRMED,
}


def _occurrence_starts_at(
    *,
    occurrence_date: date,
    template: ScheduleTemplate,
) -> datetime:
    value = datetime.combine(occurrence_date, template.start_time)
    if settings.USE_TZ:
        return make_school_aware(value)
    return value


def _first_matching_date(*, from_date: date, weekday: int) -> date:
    return from_date + timedelta(days=(weekday - from_date.weekday()) % 7)


@transaction.atomic
def validate_reschedule_template_occurrences(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor,
    now: datetime,
) -> None:
    """Reject a replacement that would create a later generation conflict.

    ``reschedule_lesson`` already rejects materialized lesson overlaps. This
    preflight covers the complementary case: an active recurring template has
    a cross-type occurrence in the target interval, but that occurrence has
    not been materialized yet.

    The canonical interactive reschedule workflow calls this before the
    low-level scheduling transition. Locking the group serializes the check
    with lesson generation, which takes the same group lock.
    """
    require_permission(
        actor,
        "scheduling.change_lesson",
        "Lesson reschedule permission is required.",
    )

    # Preserve the validation precedence of the low-level service. Invalid
    # intervals/states are deliberately left for reschedule_lesson() to report.
    if new_ends_at <= new_starts_at or new_starts_at <= now:
        return

    source_ref = Lesson.objects.only("group_id").get(pk=lesson_id)
    TrainingGroup.objects.select_for_update().get(pk=source_ref.group_id)
    source = (
        Lesson.objects.select_for_update()
        .only("id", "group_id", "lesson_type_id", "status")
        .get(pk=lesson_id)
    )
    if source.status not in _RESCHEDULABLE_STATUSES:
        return

    # Keep the existing concrete-lesson error as the first conflict shown to
    # the caller; the low-level service will render the established message.
    if (
        Lesson.objects.filter(
            group_id=source.group_id,
            starts_at__lt=new_ends_at,
            ends_at__gt=new_starts_at,
        )
        .exclude(pk=source.id)
        .exclude(status=Lesson.Status.CANCELLED)
        .exists()
    ):
        return

    templates = (
        ScheduleTemplate.objects.filter(
            group_id=source.group_id,
            is_active=True,
        )
        .exclude(lesson_type_id=source.lesson_type_id)
        .select_related("lesson_type")
        .order_by("valid_from", "id")
    )

    for template in templates:
        # An overlapping occurrence may begin before the local date of the
        # replacement when a template crosses midnight. Search back by the
        # template duration, then apply the exact timestamp overlap test.
        search_from = school_date(
            new_starts_at - timedelta(minutes=template.duration_minutes)
        )
        search_until = school_date(new_ends_at)
        effective_from = max(search_from, template.valid_from)
        effective_until = search_until
        if template.valid_until is not None:
            effective_until = min(effective_until, template.valid_until)
        if effective_until < effective_from:
            continue

        occurrence_date = _first_matching_date(
            from_date=effective_from,
            weekday=template.weekday,
        )
        while occurrence_date <= effective_until:
            starts_at = _occurrence_starts_at(
                occurrence_date=occurrence_date,
                template=template,
            )
            ends_at = starts_at + timedelta(minutes=template.duration_minutes)
            occurrence_date += timedelta(days=7)

            if starts_at >= new_ends_at or ends_at <= new_starts_at:
                continue

            # Any own materialization, including an authoritative CANCELLED
            # occurrence created by skip_template_occurrence(), closes the slot
            # for generate_lessons().
            if Lesson.objects.filter(
                source_template=template,
                starts_at=starts_at,
            ).exists():
                continue

            raise ValidationError(
                {
                    "new_starts_at": (
                        "Replacement interval overlaps unmaterialized active "
                        "schedule template occurrence "
                        f'"{template.lesson_type.name}" at '
                        f"{starts_at.isoformat()}. Skip the conflicting "
                        "template occurrence first or choose another time."
                    )
                }
            )
