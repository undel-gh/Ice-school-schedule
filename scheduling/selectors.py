from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.conf import settings

from core.time import make_school_aware

from attendance.models import Attendance
from subscriptions.models import AttendanceCoverage

from .models import Lesson, LessonResponse, LessonRosterEntry


@dataclass(frozen=True, slots=True)
class StudentLessonView:
    lesson: Lesson
    response_status: str | None
    attendance_status: str | None
    coverage: AttendanceCoverage | None


@dataclass(frozen=True, slots=True)
class CoachLessonView:
    lesson: Lesson
    roster_count: int
    yes_count: int
    no_count: int
    no_response_count: int


def _date_range_bounds(
    *,
    from_date: date,
    until_date: date,
) -> tuple[datetime, datetime]:
    if until_date < from_date:
        raise ValueError("until_date must be on or after from_date.")

    start = datetime.combine(from_date, time.min)
    end = datetime.combine(until_date + timedelta(days=1), time.min)

    if settings.USE_TZ:
        start = make_school_aware(start)
        end = make_school_aware(end)

    return start, end


def get_student_schedule(
    *,
    student_id: UUID,
    from_date: date,
    until_date: date,
) -> tuple[StudentLessonView, ...]:
    """
    Return published/history lessons visible to one student.

    Authorization is intentionally outside this selector. Visibility is based
    on the active LessonRosterEntry snapshot, not current group membership.
    """
    start, end = _date_range_bounds(
        from_date=from_date,
        until_date=until_date,
    )

    lessons = list(
        Lesson.objects.filter(
            roster_entries__student_id=student_id,
            roster_entries__is_active=True,
            starts_at__gte=start,
            starts_at__lt=end,
        )
        .exclude(status=Lesson.Status.DRAFT)
        .select_related(
            "group",
            "lesson_type",
            "coach",
            "venue",
            "replacement_lesson",
        )
        .distinct()
        .order_by("starts_at", "id")
    )
    if not lessons:
        return ()

    lesson_ids = [lesson.id for lesson in lessons]
    responses = {
        response.lesson_id: response.status
        for response in LessonResponse.objects.filter(
            lesson_id__in=lesson_ids,
            student_id=student_id,
        )
    }
    attendances = list(
        Attendance.objects.filter(
            lesson_id__in=lesson_ids,
            student_id=student_id,
        )
    )
    attendance_by_lesson = {
        attendance.lesson_id: attendance
        for attendance in attendances
    }

    coverage_by_attendance = {
        coverage.attendance_id: coverage
        for coverage in AttendanceCoverage.objects.filter(
            attendance_id__in=[
                attendance.id for attendance in attendances
            ],
            reversed_at__isnull=True,
        ).select_related(
            "subscription_allowance",
            "subscription_allowance__subscription",
            "one_time_entitlement",
            "makeup_entitlement",
        )
    }

    return tuple(
        StudentLessonView(
            lesson=lesson,
            response_status=responses.get(lesson.id),
            attendance_status=(
                attendance_by_lesson[lesson.id].status
                if lesson.id in attendance_by_lesson
                else None
            ),
            coverage=(
                coverage_by_attendance.get(
                    attendance_by_lesson[lesson.id].id
                )
                if lesson.id in attendance_by_lesson
                else None
            ),
        )
        for lesson in lessons
    )


def get_coach_schedule(
    *,
    coach_id: UUID,
    from_date: date,
    until_date: date,
) -> tuple[CoachLessonView, ...]:
    """Return lessons and operational RSVP counts for one coach."""
    start, end = _date_range_bounds(
        from_date=from_date,
        until_date=until_date,
    )

    lessons = list(
        Lesson.objects.filter(
            coach_id=coach_id,
            starts_at__gte=start,
            starts_at__lt=end,
        )
        .exclude(status=Lesson.Status.DRAFT)
        .select_related("group", "lesson_type", "coach", "venue")
        .order_by("starts_at", "id")
    )
    if not lessons:
        return ()

    lesson_ids = [lesson.id for lesson in lessons]
    roster_pairs = list(
        LessonRosterEntry.objects.filter(
            lesson_id__in=lesson_ids,
            is_active=True,
        ).values_list("lesson_id", "student_id")
    )

    roster_students: dict[UUID, set[UUID]] = {}
    for lesson_id, student_id in roster_pairs:
        roster_students.setdefault(lesson_id, set()).add(student_id)

    response_rows = LessonResponse.objects.filter(
        lesson_id__in=lesson_ids,
    ).values_list("lesson_id", "student_id", "status")

    yes: dict[UUID, int] = {}
    no: dict[UUID, int] = {}
    for lesson_id, student_id, status in response_rows:
        if student_id not in roster_students.get(lesson_id, set()):
            continue
        if status == LessonResponse.Status.YES:
            yes[lesson_id] = yes.get(lesson_id, 0) + 1
        elif status == LessonResponse.Status.NO:
            no[lesson_id] = no.get(lesson_id, 0) + 1

    result = []
    for lesson in lessons:
        roster_count = len(roster_students.get(lesson.id, set()))
        yes_count = yes.get(lesson.id, 0)
        no_count = no.get(lesson.id, 0)
        result.append(
            CoachLessonView(
                lesson=lesson,
                roster_count=roster_count,
                yes_count=yes_count,
                no_count=no_count,
                no_response_count=roster_count - yes_count - no_count,
            )
        )

    return tuple(result)
