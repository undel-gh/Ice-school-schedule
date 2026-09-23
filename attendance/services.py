from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import record_event
from core.permissions import (
    require_lesson_coach_or_permission,
    require_permission,
    require_student_access,
)
from core.time import school_date
from scheduling.models import Lesson, LessonResponse, LessonRosterEntry
from subscriptions.balances import locked_eligible_source_allowance
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
)
from subscriptions.services import (
    assign_attendance_coverage,
    reverse_attendance_coverage,
)

from .models import AbsenceJustification, Attendance

User = get_user_model()


_ALLOWED_LESSON_STATUSES = {
    Lesson.Status.CONFIRMED,
    Lesson.Status.COMPLETED,
}


def _audit(
    *,
    event_type: str,
    attendance: Attendance,
    actor: User,
    payload: dict | None = None,
    correlation_id: UUID | None = None,
) -> None:
    values = {
        "event_type": event_type,
        "actor": actor,
        "aggregate_type": "Attendance",
        "aggregate_id": attendance.id,
        "payload": payload or {},
    }
    if correlation_id is not None:
        values["correlation_id"] = correlation_id
    record_event(**values)


def _assert_actor_can_mark(*, lesson: Lesson, actor: User) -> None:
    require_lesson_coach_or_permission(
        actor=actor,
        lesson=lesson,
        permission="attendance.change_attendance",
        message=(
            "Only the lesson coach or a user with attendance change "
            "permission may mark attendance."
        ),
    )


def _validate_lesson_for_attendance(
    *,
    lesson: Lesson,
    actor: User,
    now: datetime,
) -> None:
    _assert_actor_can_mark(lesson=lesson, actor=actor)

    if lesson.status not in _ALLOWED_LESSON_STATUSES:
        raise ValidationError(
            {
                "lesson": (
                    "Attendance may only be changed for CONFIRMED or "
                    "COMPLETED lessons."
                )
            }
        )
    if now < lesson.starts_at:
        raise ValidationError(
            {"lesson": "Attendance cannot be marked before the lesson starts."}
        )


def _active_coverage(
    attendance_id: UUID,
) -> AttendanceCoverage | None:
    return AttendanceCoverage.objects.filter(
        attendance_id=attendance_id,
        reversed_at__isnull=True,
    ).first()


def _revoke_verified_medical_justifications_for_present_correction(
    *,
    attendance: Attendance,
    actor: User,
    now: datetime,
    correlation_id: UUID,
) -> None:
    justifications = list(
        AbsenceJustification.objects.select_for_update().filter(
            student_id=attendance.student_id,
            lesson_id=attendance.lesson_id,
            type=AbsenceJustification.Type.MEDICAL,
            status=AbsenceJustification.Status.VERIFIED,
        )
    )
    if not justifications:
        return

    justification_ids = [item.id for item in justifications]
    entitlements = list(
        MakeupEntitlement.objects.select_for_update().filter(
            source_justification_id__in=justification_ids,
            reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
            cancelled_at__isnull=True,
        )
    )
    for entitlement in entitlements:
        if AttendanceCoverage.objects.filter(
            makeup_entitlement=entitlement,
            reversed_at__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "attendance": (
                        "Attendance cannot be corrected to PRESENT because "
                        "the medical make-up is already used. Rebind or "
                        "reverse that coverage first."
                    )
                }
            )

    for entitlement in entitlements:
        entitlement.cancelled_at = now
        entitlement.cancelled_by = actor
        entitlement.save(update_fields=["cancelled_at", "cancelled_by"])
        record_event(
            event_type="MakeupEntitlementCancelled",
            actor=actor,
            aggregate_type="MakeupEntitlement",
            aggregate_id=entitlement.id,
            correlation_id=correlation_id,
            payload={
                "source_justification_id": str(
                    entitlement.source_justification_id
                ),
                "attendance_id": str(attendance.id),
                "cancelled_at": now.isoformat(),
                "reason": "attendance_corrected_to_present",
            },
        )

    for justification in justifications:
        justification.status = AbsenceJustification.Status.REVOKED
        justification.revoked_at = now
        justification.revoked_by = actor
        justification.save(
            update_fields=["status", "revoked_at", "revoked_by"]
        )
        record_event(
            event_type="AbsenceJustificationRevoked",
            actor=actor,
            aggregate_type="AbsenceJustification",
            aggregate_id=justification.id,
            correlation_id=correlation_id,
            payload={
                "student_id": str(justification.student_id),
                "lesson_id": str(justification.lesson_id),
                "reason": "attendance_corrected_to_present",
                "cancelled_makeup_count": sum(
                    1
                    for entitlement in entitlements
                    if entitlement.source_justification_id == justification.id
                ),
            },
        )


@transaction.atomic
def set_attendance(
    *,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    actor: User,
    now: datetime,
) -> Attendance:
    correlation_id = uuid4()
    if status not in Attendance.Status.values:
        raise ValidationError({"status": "Unsupported attendance status."})

    lesson = (
        Lesson.objects.select_for_update()
        .select_related("coach__user")
        .get(pk=lesson_id)
    )
    _validate_lesson_for_attendance(
        lesson=lesson,
        actor=actor,
        now=now,
    )

    try:
        roster_entry = (
            LessonRosterEntry.objects.select_for_update()
            .get(
                lesson_id=lesson.id,
                student_id=student_id,
                is_active=True,
            )
        )
    except LessonRosterEntry.DoesNotExist as exc:
        raise ValidationError(
            {
                "student": (
                    "Student is not an active participant of this lesson."
                )
            }
        ) from exc

    attendance = (
        Attendance.objects.select_for_update()
        .filter(
            lesson_id=lesson.id,
            student_id=roster_entry.student_id,
        )
        .first()
    )

    if attendance is None:
        attendance = Attendance.objects.create(
            lesson_id=lesson.id,
            student_id=roster_entry.student_id,
            status=status,
            marked_at=now,
            marked_by=actor,
            updated_by=actor,
        )

        if status == Attendance.Status.PRESENT:
            coverage = assign_attendance_coverage(
                attendance_id=attendance.id,
                actor=actor,
                correlation_id=correlation_id,
            )
            event_type = "AttendanceMarkedPresent"
            coverage_state = "covered" if coverage is not None else "uncovered"
        else:
            event_type = "AttendanceMarkedAbsent"
            coverage_state = "not_applicable"

        _audit(
            event_type=event_type,
            attendance=attendance,
            actor=actor,
            payload={
                "lesson_id": str(lesson.id),
                "student_id": str(roster_entry.student_id),
                "status": status,
                "coverage": coverage_state,
            },
            correlation_id=correlation_id,
        )
        if (
            status == Attendance.Status.PRESENT
            and coverage is None
        ):
            _audit(
                event_type="AttendanceUncovered",
                attendance=attendance,
                actor=actor,
                payload={
                    "lesson_id": str(lesson.id),
                    "student_id": str(roster_entry.student_id),
                },
                correlation_id=correlation_id,
            )
        return attendance

    if attendance.status == status:
        return attendance

    previous_status = attendance.status

    if (
        previous_status == Attendance.Status.PRESENT
        and status == Attendance.Status.ABSENT
    ):
        coverage = _active_coverage(attendance.id)
        if coverage is not None:
            reverse_attendance_coverage(
                coverage_id=coverage.id,
                actor=actor,
                correlation_id=correlation_id,
            )

        attendance.status = Attendance.Status.ABSENT
        attendance.updated_by = actor
        attendance.save(
            update_fields=["status", "updated_by", "updated_at"]
        )
        _audit(
            event_type="AttendanceCorrectedToAbsent",
            attendance=attendance,
            actor=actor,
            payload={
                "lesson_id": str(lesson.id),
                "student_id": str(roster_entry.student_id),
                "from_status": previous_status,
                "to_status": status,
            },
            correlation_id=correlation_id,
        )
        return attendance

    if (
        previous_status == Attendance.Status.ABSENT
        and status == Attendance.Status.PRESENT
    ):
        _revoke_verified_medical_justifications_for_present_correction(
            attendance=attendance,
            actor=actor,
            now=now,
            correlation_id=correlation_id,
        )

        attendance.status = Attendance.Status.PRESENT
        attendance.updated_by = actor
        attendance.save(
            update_fields=["status", "updated_by", "updated_at"]
        )

        coverage = assign_attendance_coverage(
            attendance_id=attendance.id,
            actor=actor,
            correlation_id=correlation_id,
        )
        _audit(
            event_type="AttendanceCorrectedToPresent",
            attendance=attendance,
            actor=actor,
            payload={
                "lesson_id": str(lesson.id),
                "student_id": str(roster_entry.student_id),
                "from_status": previous_status,
                "to_status": status,
                "coverage": (
                    "covered" if coverage is not None else "uncovered"
                ),
            },
            correlation_id=correlation_id,
        )
        if coverage is None:
            _audit(
                event_type="AttendanceUncovered",
                attendance=attendance,
                actor=actor,
                payload={
                    "lesson_id": str(lesson.id),
                    "student_id": str(roster_entry.student_id),
                },
                correlation_id=correlation_id,
            )
        return attendance

    raise ValidationError(
        {
            "status": (
                f"Unsupported attendance transition: "
                f"{previous_status} -> {status}."
            )
        }
    )


@transaction.atomic
def mark_expected_present(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
    confirmed: bool,
) -> int:
    if not confirmed:
        raise ValidationError(
            {"confirmed": "Bulk attendance marking requires confirmation."}
        )

    lesson = (
        Lesson.objects.select_for_update()
        .select_related("coach__user")
        .get(pk=lesson_id)
    )
    _validate_lesson_for_attendance(
        lesson=lesson,
        actor=actor,
        now=now,
    )

    expected_student_ids = (
        LessonResponse.objects.filter(
            lesson_id=lesson.id,
            status=LessonResponse.Status.YES,
            student_id__in=LessonRosterEntry.objects.filter(
                lesson_id=lesson.id,
                is_active=True,
            ).values("student_id"),
        )
        .exclude(
            student_id__in=Attendance.objects.filter(
                lesson_id=lesson.id,
            ).values("student_id")
        )
        .order_by("student_id")
        .values_list("student_id", flat=True)
    )

    count = 0
    for student_id in list(expected_student_ids):
        set_attendance(
            lesson_id=lesson.id,
            student_id=student_id,
            status=Attendance.Status.PRESENT,
            actor=actor,
            now=now,
        )
        count += 1

    return count


@transaction.atomic
def mark_remaining_absent(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
) -> int:
    lesson = (
        Lesson.objects.select_for_update()
        .select_related("coach__user")
        .get(pk=lesson_id)
    )
    _validate_lesson_for_attendance(
        lesson=lesson,
        actor=actor,
        now=now,
    )

    student_ids = list(
        LessonRosterEntry.objects.filter(
            lesson_id=lesson.id,
            is_active=True,
        )
        .exclude(
            student_id__in=Attendance.objects.filter(
                lesson_id=lesson.id,
            ).values("student_id")
        )
        .order_by("student_id")
        .values_list("student_id", flat=True)
    )

    count = 0
    for student_id in student_ids:
        set_attendance(
            lesson_id=lesson.id,
            student_id=student_id,
            status=Attendance.Status.ABSENT,
            actor=actor,
            now=now,
        )
        count += 1

    return count


@transaction.atomic
def submit_attendance(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
) -> Lesson:
    lesson = (
        Lesson.objects.select_for_update()
        .select_related("coach__user")
        .get(pk=lesson_id)
    )
    _assert_actor_can_mark(lesson=lesson, actor=actor)

    if lesson.status != Lesson.Status.COMPLETED:
        raise ValidationError(
            {"lesson": "Only a COMPLETED lesson can be submitted."}
        )

    active_student_ids = LessonRosterEntry.objects.filter(
        lesson_id=lesson.id,
        is_active=True,
    ).values("student_id")
    marked_student_ids = Attendance.objects.filter(
        lesson_id=lesson.id,
        student_id__in=active_student_ids,
    ).values("student_id")

    unmarked_count = (
        LessonRosterEntry.objects.filter(
            lesson_id=lesson.id,
            is_active=True,
        )
        .exclude(student_id__in=marked_student_ids)
        .count()
    )
    if unmarked_count:
        raise ValidationError(
            {
                "attendance": (
                    f"{unmarked_count} active roster participant(s) "
                    "remain unmarked."
                )
            }
        )

    lesson.status = Lesson.Status.CLOSED
    lesson.attendance_submitted_at = now
    lesson.attendance_submitted_by = actor
    lesson.save(
        update_fields=[
            "status",
            "attendance_submitted_at",
            "attendance_submitted_by",
            "updated_at",
        ]
    )

    record_event(
        event_type="LessonAttendanceSubmitted",
        actor=actor,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload={"submitted_at": now.isoformat()},
    )
    return lesson


@transaction.atomic
def reopen_attendance(
    *,
    lesson_id: UUID,
    actor: User,
    reason: str,
) -> Lesson:
    require_permission(
        actor,
        "scheduling.change_lesson",
        "Lesson change permission is required to reopen attendance.",
    )
    if not reason.strip():
        raise ValidationError({"reason": "Reopen reason is required."})

    lesson = Lesson.objects.select_for_update().get(pk=lesson_id)
    if lesson.status != Lesson.Status.CLOSED:
        raise ValidationError(
            {"lesson": "Only a CLOSED lesson can be reopened."}
        )

    lesson.status = Lesson.Status.COMPLETED
    lesson.attendance_submitted_at = None
    lesson.attendance_submitted_by = None
    lesson.save(
        update_fields=[
            "status",
            "attendance_submitted_at",
            "attendance_submitted_by",
            "updated_at",
        ]
    )

    record_event(
        event_type="LessonAttendanceReopened",
        actor=actor,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload={"reason": reason.strip()},
    )
    return lesson



def _assert_medical_reviewer(actor: User) -> None:
    require_permission(
        actor,
        "attendance.change_absencejustification",
        "Medical absence review permission is required.",
    )


def _find_medical_source_allowance(
    *,
    student_id: UUID,
    lesson: Lesson,
):
    selected = locked_eligible_source_allowance(
        student_id=student_id,
        category=lesson.lesson_type.subscription_category,
        source_date=school_date(lesson.starts_at),
    )
    if selected is None:
        return None
    allowance, subscription, _ = selected
    return allowance, subscription


@transaction.atomic
def declare_medical_absence(
    *,
    student_id: UUID,
    lesson_id: UUID,
    actor: User,
) -> AbsenceJustification:
    require_student_access(
        actor=actor,
        student_id=student_id,
    )

    attendance = (
        Attendance.objects.select_for_update()
        .select_related("lesson")
        .filter(
            lesson_id=lesson_id,
            student_id=student_id,
        )
        .first()
    )
    if attendance is None or attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {
                "attendance": (
                    "Medical absence can only be declared for an "
                    "Attendance=ABSENT record."
                )
            }
        )

    existing = (
        AbsenceJustification.objects.select_for_update()
        .filter(
            student_id=student_id,
            lesson_id=lesson_id,
            type=AbsenceJustification.Type.MEDICAL,
        )
        .first()
    )
    if existing is not None:
        if existing.status == AbsenceJustification.Status.REVOKED:
            existing.status = AbsenceJustification.Status.PENDING
            existing.reviewed_at = None
            existing.reviewed_by = None
            existing.revoked_at = None
            existing.revoked_by = None
            existing.declared_at = timezone.now()
            existing.declared_by = actor
            existing.save(
                update_fields=[
                    "status",
                    "reviewed_at",
                    "reviewed_by",
                    "revoked_at",
                    "revoked_by",
                    "declared_at",
                    "declared_by",
                ]
            )
            record_event(
                event_type="AbsenceJustificationRedeclared",
                actor=actor,
                aggregate_type="AbsenceJustification",
                aggregate_id=existing.id,
                payload={
                    "student_id": str(student_id),
                    "lesson_id": str(lesson_id),
                    "type": existing.type,
                },
            )
        return existing

    justification = AbsenceJustification.objects.create(
        student_id=student_id,
        lesson_id=lesson_id,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
        verification_method=(
            AbsenceJustification.VerificationMethod.IN_PERSON
        ),
        declared_by=actor,
    )
    record_event(
        event_type="AbsenceJustificationDeclared",
        actor=actor,
        aggregate_type="AbsenceJustification",
        aggregate_id=justification.id,
        payload={
            "student_id": str(student_id),
            "lesson_id": str(lesson_id),
            "type": justification.type,
        },
    )
    return justification


@transaction.atomic
def verify_medical_absence(
    *,
    justification_id: UUID,
    actor: User,
    valid_until: date,
    now: datetime | None = None,
) -> AbsenceJustification:
    _assert_medical_reviewer(actor)
    reviewed_at = now or timezone.now()

    justification_ref = AbsenceJustification.objects.only(
        "lesson_id",
        "student_id",
    ).get(pk=justification_id)
    Lesson.objects.select_for_update().get(pk=justification_ref.lesson_id)
    attendance = (
        Attendance.objects.select_for_update()
        .filter(
            lesson_id=justification_ref.lesson_id,
            student_id=justification_ref.student_id,
        )
        .first()
    )
    justification = (
        AbsenceJustification.objects.select_for_update()
        .select_related("lesson__lesson_type")
        .get(pk=justification_id)
    )
    if justification.type != AbsenceJustification.Type.MEDICAL:
        raise ValidationError(
            {"justification": "Only MEDICAL justification is supported."}
        )
    if justification.status != AbsenceJustification.Status.PENDING:
        raise ValidationError(
            {
                "justification": (
                    "Only a PENDING justification can be verified."
                )
            }
        )

    if attendance is None or attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {
                "attendance": (
                    "Medical justification can only be verified while "
                    "Attendance remains ABSENT."
                )
            }
        )

    justification.status = AbsenceJustification.Status.VERIFIED
    justification.reviewed_at = reviewed_at
    justification.reviewed_by = actor
    justification.save(
        update_fields=["status", "reviewed_at", "reviewed_by"]
    )

    source = _find_medical_source_allowance(
        student_id=justification.student_id,
        lesson=justification.lesson,
    )

    entitlement = None
    if source is not None:
        allowance, subscription = source
        valid_from = subscription.valid_until + timedelta(days=1)
        if valid_until < valid_from:
            raise ValidationError(
                {
                    "valid_until": (
                        "Medical make-up expiry must be on or after "
                        f"{valid_from.isoformat()}."
                    )
                }
            )

        entitlement = (
            MakeupEntitlement.objects.select_for_update()
            .filter(
                student_id=justification.student_id,
                source_lesson_id=justification.lesson_id,
                reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
            )
            .first()
        )
        event_type = "MakeupEntitlementGranted"
        if entitlement is None:
            entitlement = MakeupEntitlement.objects.create(
                student_id=justification.student_id,
                source_lesson_id=justification.lesson_id,
                source_subscription_allowance=allowance,
                source_justification=justification,
                category=allowance.category,
                reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
                valid_from=valid_from,
                valid_until=valid_until,
                created_by=actor,
            )
        else:
            if entitlement.cancelled_at is None:
                raise ValidationError(
                    {
                        "justification": (
                            "An active medical make-up already exists for "
                            "this absence."
                        )
                    }
                )
            entitlement.source_subscription_allowance = allowance
            entitlement.source_justification = justification
            entitlement.category = allowance.category
            entitlement.valid_from = valid_from
            entitlement.valid_until = valid_until
            entitlement.cancelled_at = None
            entitlement.cancelled_by = None
            entitlement.save(
                update_fields=[
                    "source_subscription_allowance",
                    "source_justification",
                    "category",
                    "valid_from",
                    "valid_until",
                    "cancelled_at",
                    "cancelled_by",
                ]
            )
            event_type = "MakeupEntitlementReactivated"

        record_event(
            event_type=event_type,
            actor=actor,
            aggregate_type="MakeupEntitlement",
            aggregate_id=entitlement.id,
            payload={
                "student_id": str(justification.student_id),
                "source_lesson_id": str(justification.lesson_id),
                "source_allowance_id": str(allowance.id),
                "category": allowance.category,
                "valid_from": valid_from.isoformat(),
                "valid_until": valid_until.isoformat(),
                "reason": entitlement.reason,
            },
        )

    record_event(
        event_type="AbsenceJustificationVerified",
        actor=actor,
        aggregate_type="AbsenceJustification",
        aggregate_id=justification.id,
        payload={
            "student_id": str(justification.student_id),
            "lesson_id": str(justification.lesson_id),
            "makeup_entitlement_id": (
                str(entitlement.id) if entitlement is not None else None
            ),
        },
    )
    return justification


@transaction.atomic
def reject_medical_absence(
    *,
    justification_id: UUID,
    actor: User,
    now: datetime | None = None,
) -> AbsenceJustification:
    _assert_medical_reviewer(actor)
    reviewed_at = now or timezone.now()

    justification = AbsenceJustification.objects.select_for_update().get(
        pk=justification_id
    )
    if justification.type != AbsenceJustification.Type.MEDICAL:
        raise ValidationError(
            {"justification": "Only MEDICAL justification is supported."}
        )
    if justification.status != AbsenceJustification.Status.PENDING:
        raise ValidationError(
            {
                "justification": (
                    "Only a PENDING justification can be rejected."
                )
            }
        )

    justification.status = AbsenceJustification.Status.REJECTED
    justification.reviewed_at = reviewed_at
    justification.reviewed_by = actor
    justification.save(
        update_fields=["status", "reviewed_at", "reviewed_by"]
    )

    record_event(
        event_type="AbsenceJustificationRejected",
        actor=actor,
        aggregate_type="AbsenceJustification",
        aggregate_id=justification.id,
        payload={
            "student_id": str(justification.student_id),
            "lesson_id": str(justification.lesson_id),
        },
    )
    return justification


@transaction.atomic
def revoke_medical_absence(
    *,
    justification_id: UUID,
    actor: User,
    now: datetime | None = None,
) -> AbsenceJustification:
    _assert_medical_reviewer(actor)
    revoked_at = now or timezone.now()

    justification = AbsenceJustification.objects.select_for_update().get(
        pk=justification_id
    )
    if justification.type != AbsenceJustification.Type.MEDICAL:
        raise ValidationError(
            {"justification": "Only MEDICAL justification is supported."}
        )
    if justification.status != AbsenceJustification.Status.VERIFIED:
        raise ValidationError(
            {
                "justification": (
                    "Only a VERIFIED justification can be revoked."
                )
            }
        )

    entitlements = list(
        MakeupEntitlement.objects.select_for_update().filter(
            source_justification=justification,
            reason=MakeupEntitlement.Reason.MEDICAL_VERIFIED,
            cancelled_at__isnull=True,
        )
    )
    for entitlement in entitlements:
        if AttendanceCoverage.objects.filter(
            makeup_entitlement=entitlement,
            reversed_at__isnull=True,
        ).exists():
            raise ValidationError(
                {
                    "justification": (
                        "Medical make-up is already used by an active "
                        "coverage; reverse or rebind it before revocation."
                    )
                }
            )

    for entitlement in entitlements:
        entitlement.cancelled_at = revoked_at
        entitlement.cancelled_by = actor
        entitlement.save(
            update_fields=["cancelled_at", "cancelled_by"]
        )
        record_event(
            event_type="MakeupEntitlementCancelled",
            actor=actor,
            aggregate_type="MakeupEntitlement",
            aggregate_id=entitlement.id,
            payload={
                "source_justification_id": str(justification.id),
                "cancelled_at": revoked_at.isoformat(),
            },
        )

    justification.status = AbsenceJustification.Status.REVOKED
    justification.revoked_at = revoked_at
    justification.revoked_by = actor
    justification.save(
        update_fields=["status", "revoked_at", "revoked_by"]
    )

    record_event(
        event_type="AbsenceJustificationRevoked",
        actor=actor,
        aggregate_type="AbsenceJustification",
        aggregate_id=justification.id,
        payload={
            "student_id": str(justification.student_id),
            "lesson_id": str(justification.lesson_id),
            "cancelled_makeup_count": len(entitlements),
        },
    )
    return justification
