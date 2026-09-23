from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditEvent
from scheduling.models import Lesson, LessonRosterEntry
from subscriptions.models import AttendanceCoverage
from subscriptions.services import (
    assign_attendance_coverage,
    reverse_attendance_coverage,
)

from .models import Attendance

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
) -> None:
    AuditEvent.objects.create(
        event_type=event_type,
        actor=actor,
        aggregate_type="Attendance",
        aggregate_id=attendance.id,
        payload=payload or {},
    )


def _assert_actor_can_mark(*, lesson: Lesson, actor: User) -> None:
    if actor.is_superuser or actor.is_staff:
        return
    if lesson.coach.user_id == actor.id:
        return
    raise PermissionDenied(
        "Only the lesson coach or an administrator may mark attendance."
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


@transaction.atomic
def set_attendance(
    *,
    lesson_id: UUID,
    student_id: UUID,
    status: str,
    actor: User,
    now: datetime,
) -> Attendance:
    if status not in Attendance.Status.values:
        raise ValidationError({"status": "Unsupported attendance status."})

    lesson = (
        Lesson.objects.select_related("coach__user")
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
        )
        return attendance

    if attendance.status == status:
        attendance.updated_by = actor
        attendance.save(update_fields=["updated_by", "updated_at"])
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
        )
        return attendance

    if (
        previous_status == Attendance.Status.ABSENT
        and status == Attendance.Status.PRESENT
    ):
        attendance.status = Attendance.Status.PRESENT
        attendance.updated_by = actor
        attendance.save(
            update_fields=["status", "updated_by", "updated_at"]
        )

        coverage = assign_attendance_coverage(
            attendance_id=attendance.id,
            actor=actor,
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

    AuditEvent.objects.create(
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
    if not (actor.is_staff or actor.is_superuser):
        raise PermissionDenied(
            "Only an administrator may reopen submitted attendance."
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

    AuditEvent.objects.create(
        event_type="LessonAttendanceReopened",
        actor=actor,
        aggregate_type="Lesson",
        aggregate_id=lesson.id,
        payload={"reason": reason.strip()},
    )
    return lesson
