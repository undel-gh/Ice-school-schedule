from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.models import StudentAccess
from audit.models import AuditEvent
from subscriptions.models import (
    MakeupEntitlement,
    Subscription,
    SubscriptionAllowance,
    SubscriptionLedgerEntry,
)

from .models import (
    GroupMembership,
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


def _lesson_date(lesson: Lesson):
    if timezone.is_aware(lesson.starts_at):
        return timezone.localtime(lesson.starts_at).date()
    return lesson.starts_at.date()


def _audit_lesson(
    *,
    event_type: str,
    lesson: Lesson,
    actor: User | None,
    payload: dict | None = None,
) -> None:
    AuditEvent.objects.create(
        event_type=event_type,
        actor=actor,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload=payload or {},
    )


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

    lesson_date = _lesson_date(lesson)

    memberships = (
        GroupMembership.objects.filter(
            group_id=lesson.group_id,
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

    AuditEvent.objects.create(
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


def _locked_source_allowance_for_reschedule(
    *,
    student_id: UUID,
    category: str,
    source_date,
) -> tuple[SubscriptionAllowance, Subscription, int] | None:
    candidate_ids = list(
        SubscriptionAllowance.objects.filter(
            subscription__student_id=student_id,
            category=category,
            subscription__cancelled_at__isnull=True,
            subscription__valid_from__lte=source_date,
            subscription__valid_until__gte=source_date,
        )
        .order_by(
            "subscription__valid_until",
            "subscription__valid_from",
            "subscription__created_at",
            "id",
        )
        .values_list("id", flat=True)
    )

    for allowance_id in candidate_ids:
        allowance = SubscriptionAllowance.objects.select_for_update().get(
            pk=allowance_id
        )
        subscription = Subscription.objects.get(
            pk=allowance.subscription_id
        )
        balance = SubscriptionLedgerEntry.objects.filter(
            allowance=allowance
        ).aggregate(balance=Sum("delta"))["balance"]
        balance = int(balance or 0)

        if subscription.cancelled_at is not None:
            continue
        if allowance.category != category:
            continue
        if not (
            subscription.valid_from
            <= source_date
            <= subscription.valid_until
        ):
            continue
        if balance <= 0:
            continue

        return allowance, subscription, balance

    return None


@transaction.atomic
def reschedule_lesson(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor: User,
    reason: str,
) -> Lesson:
    _validate_cancellation_reason(reason)
    if new_ends_at <= new_starts_at:
        raise ValidationError(
            {"new_ends_at": "Replacement lesson must end after it starts."}
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

    source.status = Lesson.Status.CANCELLED
    source.cancelled_at = timezone.now()
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

    source_date = _lesson_date(source)
    replacement_date = _lesson_date(replacement)
    category = source.lesson_type.subscription_category

    yes_student_ids = list(
        LessonResponse.objects.filter(
            lesson=source,
            status=LessonResponse.Status.YES,
        ).values_list("student_id", flat=True)
    )

    makeup_count = 0
    for student_id in yes_student_ids:
        selected = _locked_source_allowance_for_reschedule(
            student_id=student_id,
            category=category,
            source_date=source_date,
        )
        if selected is None:
            continue

        allowance, subscription, _ = selected
        if (
            subscription.valid_from
            <= replacement_date
            <= subscription.valid_until
        ):
            continue

        MakeupEntitlement.objects.create(
            student_id=student_id,
            source_lesson=source,
            source_subscription_allowance=allowance,
            category=category,
            reason=MakeupEntitlement.Reason.SCHOOL_RESCHEDULE,
            valid_from=replacement_date,
            valid_until=replacement_date,
            target_lesson=replacement,
            created_by=actor,
        )
        makeup_count += 1

    _audit_lesson(
        event_type="LessonRescheduled",
        lesson=source,
        actor=actor,
        payload={
            "replacement_lesson_id": str(replacement.id),
            "old_starts_at": source.starts_at.isoformat(),
            "new_starts_at": new_starts_at.isoformat(),
            "reason": reason,
            "makeup_entitlements_created": makeup_count,
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
