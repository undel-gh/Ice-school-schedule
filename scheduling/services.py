from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.time import make_school_aware, school_date as get_school_date

from accounts.models import StudentAccess
from audit.services import record_event
from .models import (
    GroupMembership,
    ScheduleTemplate,
    Lesson,
    LessonEnrollment,
    LessonResponse,
    LessonRosterEntry,
)

User = get_user_model()


@dataclass(frozen=True, slots=True)
class LessonViabilityResult:
    yes_count: int
    no_count: int
    no_response_count: int
    minimum_attendees: int
    minimum_met: bool



def _membership_overlaps(
    *,
    starts_on: date,
    ends_on: date | None,
    other: GroupMembership,
) -> bool:
    effective_end = ends_on or date.max
    other_end = other.ends_on or date.max
    return starts_on <= other_end and other.starts_on <= effective_end


@transaction.atomic
def create_group_membership(
    *,
    student_id: UUID,
    group_id: UUID,
    starts_on: date,
    ends_on: date | None,
    actor: User | None,
) -> GroupMembership:
    if ends_on is not None and ends_on < starts_on:
        raise ValidationError(
            {"ends_on": "Membership end date cannot precede start date."}
        )

    existing = list(
        GroupMembership.objects.select_for_update().filter(
            student_id=student_id,
            group_id=group_id,
        )
    )
    if any(
        _membership_overlaps(
            starts_on=starts_on,
            ends_on=ends_on,
            other=membership,
        )
        for membership in existing
    ):
        raise ValidationError(
            {
                "membership": (
                    "Membership interval overlaps an existing interval "
                    "for this student and group."
                )
            }
        )

    return GroupMembership.objects.create(
        student_id=student_id,
        group_id=group_id,
        starts_on=starts_on,
        ends_on=ends_on,
        created_by=actor,
    )


@transaction.atomic
def update_group_membership(
    *,
    membership_id: UUID,
    starts_on: date,
    ends_on: date | None,
) -> GroupMembership:
    if ends_on is not None and ends_on < starts_on:
        raise ValidationError(
            {"ends_on": "Membership end date cannot precede start date."}
        )

    membership = GroupMembership.objects.select_for_update().get(
        pk=membership_id
    )
    others = list(
        GroupMembership.objects.select_for_update()
        .filter(
            student_id=membership.student_id,
            group_id=membership.group_id,
        )
        .exclude(pk=membership.id)
    )
    if any(
        _membership_overlaps(
            starts_on=starts_on,
            ends_on=ends_on,
            other=other,
        )
        for other in others
    ):
        raise ValidationError(
            {
                "membership": (
                    "Membership interval overlaps an existing interval "
                    "for this student and group."
                )
            }
        )

    membership.starts_on = starts_on
    membership.ends_on = ends_on
    membership.save(update_fields=["starts_on", "ends_on"])
    return membership


def _validate_deadline_policy() -> tuple[int, int]:
    rsvp_minutes = settings.SCHEDULING_RSVP_DEADLINE_MINUTES_BEFORE_START
    decision_minutes = settings.SCHEDULING_DECISION_DEADLINE_MINUTES_BEFORE_START

    if decision_minutes < 0 or rsvp_minutes < 0:
        raise ValidationError(
            "Scheduling deadline lead times must be non-negative."
        )
    if rsvp_minutes < decision_minutes:
        raise ValidationError(
            "RSVP deadline lead time must be greater than or equal to "
            "decision deadline lead time."
        )
    return rsvp_minutes, decision_minutes


def _aware_datetime_for_school_date(
    *,
    school_date: date,
    local_time,
) -> datetime:
    naive = datetime.combine(school_date, local_time)
    if settings.USE_TZ:
        return make_school_aware(naive)
    return naive


def _audit_lesson(
    *,
    event_type: str,
    lesson: Lesson,
    actor: User | None,
    payload: dict | None = None,
) -> None:
    record_event(
        event_type=event_type,
        actor=actor,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload=payload,
    )



@transaction.atomic
def generate_lessons(
    *,
    template_id: UUID,
    from_date: date,
    until_date: date,
    actor: User | None = None,
) -> list[Lesson]:
    if until_date < from_date:
        raise ValidationError(
            {"until_date": "until_date must be on or after from_date."}
        )

    template = (
        ScheduleTemplate.objects.select_for_update()
        .select_related("group")
        .get(pk=template_id)
    )
    if not template.is_active:
        raise ValidationError(
            {"template": "Inactive schedule templates cannot generate lessons."}
        )

    effective_from = max(from_date, template.valid_from)
    effective_until = until_date
    if template.valid_until is not None:
        effective_until = min(effective_until, template.valid_until)

    if effective_until < effective_from:
        return []

    rsvp_minutes, decision_minutes = _validate_deadline_policy()
    created_or_existing: list[Lesson] = []
    created_ids: list[str] = []

    current = effective_from
    while current <= effective_until:
        if current.weekday() != template.weekday:
            current += timedelta(days=1)
            continue

        starts_at = _aware_datetime_for_school_date(
            school_date=current,
            local_time=template.start_time,
        )
        ends_at = starts_at + timedelta(minutes=template.duration_minutes)
        minimum_attendees = (
            template.minimum_attendees_override
            if template.minimum_attendees_override is not None
            else template.group.default_minimum_attendees
        )
        rsvp_deadline = starts_at - timedelta(minutes=rsvp_minutes)
        decision_deadline = starts_at - timedelta(minutes=decision_minutes)

        lesson, created = Lesson.objects.get_or_create(
            source_template=template,
            starts_at=starts_at,
            defaults={
                "group_id": template.group_id,
                "lesson_type_id": template.lesson_type_id,
                "coach_id": template.coach_id,
                "venue_id": template.venue_id,
                "ends_at": ends_at,
                "minimum_attendees": minimum_attendees,
                "rsvp_deadline": rsvp_deadline,
                "decision_deadline": decision_deadline,
                "status": Lesson.Status.DRAFT,
            },
        )
        created_or_existing.append(lesson)
        if created:
            created_ids.append(str(lesson.id))
        current += timedelta(days=1)

    if created_ids:
        record_event(
            event_type="LessonsGenerated",
            actor=actor,
            aggregate_type="ScheduleTemplate",
            aggregate_id=template.id,
            payload={
                "from_date": from_date.isoformat(),
                "until_date": until_date.isoformat(),
                "lesson_ids": created_ids,
            },
        )
    return created_or_existing


def publish_daily_schedule(
    *,
    school_date: date,
    now: datetime,
) -> list[Lesson]:
    start = make_school_aware(
        datetime.combine(school_date, datetime.min.time())
    )
    end = start + timedelta(days=1)
    lesson_ids = list(
        Lesson.objects.filter(
            status=Lesson.Status.DRAFT,
            starts_at__gte=start,
            starts_at__lt=end,
        )
        .order_by("starts_at", "id")
        .values_list("id", flat=True)
    )

    published = []
    for lesson_id in lesson_ids:
        try:
            lesson = publish_lesson(
                lesson_id=lesson_id,
                actor=None,
                now=now,
            )
        except ValidationError:
            current_status = Lesson.objects.filter(
                pk=lesson_id
            ).values_list("status", flat=True).first()
            if current_status != Lesson.Status.DRAFT:
                continue
            raise
        published.append(lesson)
    return published


@transaction.atomic
def add_lesson_enrollment(
    *,
    lesson_id: UUID,
    student_id: UUID,
    reason: str,
    actor: User,
) -> LessonEnrollment:
    if reason not in LessonEnrollment.Reason.values:
        raise ValidationError({"reason": "Unsupported enrollment reason."})

    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status in {
        Lesson.Status.CANCELLED,
        Lesson.Status.COMPLETED,
        Lesson.Status.CLOSED,
    }:
        raise ValidationError(
            {"lesson": "Cannot add enrollment to this lesson state."}
        )

    existing = (
        LessonEnrollment.objects.select_for_update()
        .filter(
            lesson=lesson,
            student_id=student_id,
            cancelled_at__isnull=True,
        )
        .first()
    )
    if existing is not None:
        return existing

    enrollment = LessonEnrollment.objects.create(
        lesson=lesson,
        student_id=student_id,
        reason=reason,
        created_by=actor,
    )

    if lesson.status in {
        Lesson.Status.RSVP_OPEN,
        Lesson.Status.CONFIRMED,
    }:
        roster, created = LessonRosterEntry.objects.get_or_create(
            lesson=lesson,
            student_id=student_id,
            defaults={
                "source": LessonRosterEntry.Source.ENROLLMENT,
                "lesson_enrollment": enrollment,
                "added_by": actor,
                "is_active": True,
            },
        )
        if not created:
            roster.source = LessonRosterEntry.Source.ENROLLMENT
            roster.lesson_enrollment = enrollment
            roster.is_active = True
            roster.deactivated_at = None
            roster.deactivated_by = None
            roster.save(
                update_fields=[
                    "source",
                    "lesson_enrollment",
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                ]
            )

    record_event(
        event_type="LessonEnrollmentAdded",
        actor=actor,
        aggregate_type="LessonEnrollment",
        aggregate_id=enrollment.id,
        payload={
            "lesson_id": str(lesson.id),
            "student_id": str(student_id),
            "reason": reason,
        },
    )
    return enrollment


@transaction.atomic
def publish_lesson(
    *,
    lesson_id: UUID,
    actor: User | None,
    now: datetime,
) -> Lesson:
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status != Lesson.Status.DRAFT:
        raise ValidationError(
            {"lesson": "Only a DRAFT lesson can be published."}
        )

    lesson_date = get_school_date(lesson.starts_at)

    memberships = (
        GroupMembership.objects.filter(
            group_id=lesson.group_id,
            student__is_active=True,
            starts_on__lte=lesson_date,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=lesson_date))
        .order_by("student_id", "-starts_on", "id")
    )

    selected_memberships = {}
    for membership in memberships:
        selected_memberships.setdefault(membership.student_id, membership)

    for membership in selected_memberships.values():
        roster, created = LessonRosterEntry.objects.get_or_create(
            lesson=lesson,
            student_id=membership.student_id,
            defaults={
                "source": LessonRosterEntry.Source.GROUP,
                "group_membership": membership,
                "added_by": actor,
                "is_active": True,
            },
        )
        if not created and not roster.is_active:
            roster.is_active = True
            roster.deactivated_at = None
            roster.deactivated_by = None
            roster.save(
                update_fields=[
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                ]
            )

    enrollments = (
        LessonEnrollment.objects.filter(
            lesson=lesson,
            student__is_active=True,
            cancelled_at__isnull=True,
        )
        .order_by("student_id", "created_at", "id")
    )
    for enrollment in enrollments:
        roster, created = LessonRosterEntry.objects.get_or_create(
            lesson=lesson,
            student_id=enrollment.student_id,
            defaults={
                "source": LessonRosterEntry.Source.ENROLLMENT,
                "lesson_enrollment": enrollment,
                "added_by": actor,
                "is_active": True,
            },
        )
        if not created and not roster.is_active:
            roster.is_active = True
            roster.deactivated_at = None
            roster.deactivated_by = None
            roster.save(
                update_fields=[
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                ]
            )

    lesson.status = Lesson.Status.RSVP_OPEN
    lesson.published_at = now
    lesson.save(
        update_fields=["status", "published_at", "updated_at"]
    )

    roster_count = LessonRosterEntry.objects.filter(
        lesson=lesson,
        is_active=True,
    ).count()
    _audit_lesson(
        event_type="LessonPublished",
        lesson=lesson,
        actor=actor,
        payload={
            "published_at": now.isoformat(),
            "roster_count": roster_count,
        },
    )
    return lesson


@transaction.atomic
def set_lesson_response(
    *,
    actor: User,
    student_id: UUID,
    lesson_id: UUID,
    status: str,
    now: datetime,
) -> LessonResponse:
    if status not in LessonResponse.Status.values:
        raise ValidationError({"status": "Unsupported RSVP status."})

    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status not in {
        Lesson.Status.RSVP_OPEN,
        Lesson.Status.CONFIRMED,
    }:
        raise ValidationError(
            {
                "lesson": (
                    "RSVP is only available for RSVP_OPEN or CONFIRMED "
                    "lessons."
                )
            }
        )
    if now > lesson.rsvp_deadline:
        raise ValidationError({"lesson": "RSVP deadline has passed."})

    if not StudentAccess.objects.filter(
        user=actor,
        student_id=student_id,
        is_active=True,
    ).exists():
        raise ValidationError(
            {"student": "Actor has no active access to this student."}
        )

    try:
        LessonRosterEntry.objects.select_for_update().get(
            lesson=lesson,
            student_id=student_id,
            is_active=True,
        )
    except LessonRosterEntry.DoesNotExist as exc:
        raise ValidationError(
            {"student": "Student is not an active lesson participant."}
        ) from exc

    response = (
        LessonResponse.objects.select_for_update()
        .filter(lesson=lesson, student_id=student_id)
        .first()
    )
    previous_status = response.status if response is not None else None

    if response is None:
        response = LessonResponse.objects.create(
            lesson=lesson,
            student_id=student_id,
            status=status,
            updated_by=actor,
        )
    else:
        response.status = status
        response.updated_by = actor
        response.save(
            update_fields=["status", "updated_by", "updated_at"]
        )

    record_event(
        event_type="LessonResponseChanged",
        actor=actor,
        aggregate_type="LessonResponse",
        aggregate_id=response.id,
        payload={
            "lesson_id": str(lesson.id),
            "student_id": str(student_id),
            "previous_status": previous_status,
            "status": status,
        },
    )
    return response


@transaction.atomic
def evaluate_lesson_viability(
    *,
    lesson_id: UUID,
    now: datetime,
) -> LessonViabilityResult:
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status not in {
        Lesson.Status.RSVP_OPEN,
        Lesson.Status.CONFIRMED,
    }:
        raise ValidationError(
            {
                "lesson": (
                    "Viability can only be evaluated for RSVP_OPEN or "
                    "CONFIRMED lessons."
                )
            }
        )
    if now < lesson.decision_deadline:
        raise ValidationError(
            {"lesson": "Decision deadline has not been reached yet."}
        )

    active_student_ids = list(
        LessonRosterEntry.objects.filter(
            lesson=lesson,
            is_active=True,
        ).values_list("student_id", flat=True)
    )
    responses = LessonResponse.objects.filter(
        lesson=lesson,
        student_id__in=active_student_ids,
    )

    yes_count = responses.filter(
        status=LessonResponse.Status.YES
    ).count()
    no_count = responses.filter(
        status=LessonResponse.Status.NO
    ).count()
    no_response_count = (
        len(active_student_ids) - yes_count - no_count
    )
    minimum_met = yes_count >= lesson.minimum_attendees

    lesson.decision_evaluated_at = now
    lesson.decision_yes_count = yes_count
    lesson.decision_no_count = no_count
    lesson.decision_no_response_count = no_response_count
    lesson.save(
        update_fields=[
            "decision_evaluated_at",
            "decision_yes_count",
            "decision_no_count",
            "decision_no_response_count",
            "updated_at",
        ]
    )

    _audit_lesson(
        event_type=(
            "LessonMinimumReached"
            if minimum_met
            else "LessonMinimumNotMet"
        ),
        lesson=lesson,
        actor=None,
        payload={
            "yes_count": yes_count,
            "no_count": no_count,
            "no_response_count": no_response_count,
            "minimum_attendees": lesson.minimum_attendees,
            "minimum_met": minimum_met,
            "evaluated_at": now.isoformat(),
        },
    )

    return LessonViabilityResult(
        yes_count=yes_count,
        no_count=no_count,
        no_response_count=no_response_count,
        minimum_attendees=lesson.minimum_attendees,
        minimum_met=minimum_met,
    )


@transaction.atomic
def confirm_lesson(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
) -> Lesson:
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status != Lesson.Status.RSVP_OPEN:
        raise ValidationError(
            {"lesson": "Only an RSVP_OPEN lesson can be confirmed."}
        )

    lesson.status = Lesson.Status.CONFIRMED
    lesson.confirmed_at = now
    lesson.confirmed_by = actor
    lesson.save(
        update_fields=[
            "status",
            "confirmed_at",
            "confirmed_by",
            "updated_at",
        ]
    )

    _audit_lesson(
        event_type="LessonConfirmed",
        lesson=lesson,
        actor=actor,
        payload={"confirmed_at": now.isoformat()},
    )
    return lesson


def _validate_cancellation_reason(reason: str) -> None:
    if reason not in Lesson.CancellationReason.values:
        raise ValidationError(
            {"reason": "Unsupported lesson cancellation reason."}
        )


@transaction.atomic
def cancel_lesson(
    *,
    lesson_id: UUID,
    actor: User,
    reason: str,
    now: datetime,
) -> Lesson:
    _validate_cancellation_reason(reason)
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status not in {
        Lesson.Status.RSVP_OPEN,
        Lesson.Status.CONFIRMED,
    }:
        raise ValidationError(
            {
                "lesson": (
                    "Only RSVP_OPEN or CONFIRMED lessons can be cancelled."
                )
            }
        )

    lesson.status = Lesson.Status.CANCELLED
    lesson.cancelled_at = now
    lesson.cancelled_by = actor
    lesson.cancellation_reason = reason
    lesson.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ]
    )

    _audit_lesson(
        event_type="LessonCancelled",
        lesson=lesson,
        actor=actor,
        payload={
            "cancelled_at": now.isoformat(),
            "reason": reason,
        },
    )
    return lesson


@transaction.atomic
def reschedule_lesson(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor: User,
    reason: str,
    now: datetime,
) -> Lesson:
    _validate_cancellation_reason(reason)
    if new_ends_at <= new_starts_at:
        raise ValidationError(
            {"new_ends_at": "Replacement lesson must end after it starts."}
        )
    if new_starts_at <= now:
        raise ValidationError(
            {"new_starts_at": "Replacement lesson must start in the future."}
        )

    source = (
        Lesson.objects.select_for_update()
        .select_related("lesson_type")
        .get(pk=lesson_id)
    )
    if source.status not in {
        Lesson.Status.RSVP_OPEN,
        Lesson.Status.CONFIRMED,
    }:
        raise ValidationError(
            {
                "lesson": (
                    "Only RSVP_OPEN or CONFIRMED lessons can be "
                    "rescheduled."
                )
            }
        )

    rsvp_lead = source.starts_at - source.rsvp_deadline
    decision_lead = source.starts_at - source.decision_deadline

    replacement = Lesson.objects.create(
        source_template=None,
        group_id=source.group_id,
        lesson_type_id=source.lesson_type_id,
        coach_id=source.coach_id,
        venue_id=source.venue_id,
        starts_at=new_starts_at,
        ends_at=new_ends_at,
        minimum_attendees=source.minimum_attendees,
        rsvp_deadline=new_starts_at - rsvp_lead,
        decision_deadline=new_starts_at - decision_lead,
        status=Lesson.Status.DRAFT,
    )

    source_enrollments = list(
        LessonEnrollment.objects.select_for_update().filter(
            lesson=source,
            cancelled_at__isnull=True,
        )
    )
    for enrollment in source_enrollments:
        LessonEnrollment.objects.create(
            lesson=replacement,
            student_id=enrollment.student_id,
            reason=enrollment.reason,
            created_by=actor,
        )

    source.status = Lesson.Status.CANCELLED
    source.cancelled_at = now
    source.cancelled_by = actor
    source.cancellation_reason = reason
    source.replacement_lesson = replacement
    source.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "replacement_lesson",
            "updated_at",
        ]
    )

    _audit_lesson(
        event_type="LessonRescheduled",
        lesson=source,
        actor=actor,
        payload={
            "replacement_lesson_id": str(replacement.id),
            "old_starts_at": source.starts_at.isoformat(),
            "new_starts_at": new_starts_at.isoformat(),
            "reason": reason,
        },
    )
    return replacement


@transaction.atomic
def complete_lesson(
    *,
    lesson_id: UUID,
    now: datetime,
) -> Lesson:
    """Move a finished confirmed lesson into attendance-entry state."""
    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)

    if lesson.status != Lesson.Status.CONFIRMED:
        raise ValidationError(
            {
                "lesson": (
                    "Only a CONFIRMED lesson can be completed."
                )
            }
        )
    if now < lesson.ends_at:
        raise ValidationError(
            {"lesson": "Lesson cannot be completed before it ends."}
        )

    lesson.status = Lesson.Status.COMPLETED
    lesson.completed_at = now
    lesson.save(
        update_fields=["status", "completed_at", "updated_at"]
    )

    _audit_lesson(
        event_type="LessonCompleted",
        lesson=lesson,
        actor=None,
        payload={"completed_at": now.isoformat()},
    )
    return lesson
