from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.permissions import require_lesson_coach_or_permission
from core.time import school_date as get_school_date
from django.views.decorators.http import require_POST

from accounts.models import CoachProfile, StudentAccess
from attendance.models import Attendance
from attendance.services import (
    mark_expected_present,
    mark_remaining_absent,
    set_attendance,
    submit_attendance,
)
from subscriptions.models import AttendanceCoverage

from .models import Lesson, LessonResponse, LessonRosterEntry
from .selectors import get_coach_schedule, get_student_schedule
from .services import complete_lesson, set_lesson_response

MAX_SCHEDULE_RANGE_DAYS = 366


def _parse_date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404("Invalid date.") from exc


def _active_student_accesses(request: HttpRequest):
    return (
        StudentAccess.objects.filter(
            user=request.user,
            is_active=True,
            student__is_active=True,
        )
        .select_related("student")
        .order_by("student__display_name", "student_id")
    )


def _coach_for_user(request: HttpRequest) -> CoachProfile:
    try:
        coach = request.user.coach_profile
    except CoachProfile.DoesNotExist as exc:
        raise PermissionDenied("Coach access required.") from exc
    if not coach.is_active:
        raise PermissionDenied("Coach profile is inactive.")
    return coach


def _assert_lesson_actor(request: HttpRequest, lesson: Lesson) -> None:
    require_lesson_coach_or_permission(
        actor=request.user,
        lesson=lesson,
        permission="attendance.change_attendance",
        message=(
            "Only the lesson coach or a user with attendance change "
            "permission may access this lesson."
        ),
    )


@login_required
def home(request: HttpRequest) -> HttpResponse:
    if _active_student_accesses(request).exists():
        return redirect("scheduling:student_schedule")
    try:
        coach = request.user.coach_profile
    except CoachProfile.DoesNotExist:
        coach = None
    if coach is not None and coach.is_active:
        return redirect("scheduling:coach_schedule")
    if request.user.is_staff:
        return redirect("admin:index")
    raise PermissionDenied("No active school role is assigned.")


@login_required
def student_schedule(request: HttpRequest) -> HttpResponse:
    accesses = list(_active_student_accesses(request))
    if not accesses:
        raise PermissionDenied("No active student access.")

    requested_student_id = request.GET.get("student")
    if requested_student_id:
        selected = next(
            (
                access.student
                for access in accesses
                if str(access.student_id) == requested_student_id
            ),
            None,
        )
        if selected is None:
            raise Http404("Student is not available.")
    else:
        selected = accesses[0].student

    today = get_school_date(timezone.now())
    from_date = _parse_date(request.GET.get("from"), default=today)
    until_date = _parse_date(
        request.GET.get("until"),
        default=from_date + timedelta(days=30),
    )
    if until_date < from_date:
        raise Http404("Invalid date range.")
    if (until_date - from_date).days > MAX_SCHEDULE_RANGE_DAYS:
        raise Http404("Date range is too large.")

    lessons = get_student_schedule(
        student_id=selected.id,
        from_date=from_date,
        until_date=until_date,
    )

    return render(
        request,
        "scheduling/student_schedule.html",
        {
            "students": tuple(access.student for access in accesses),
            "selected_student": selected,
            "lessons": lessons,
            "from_date": from_date,
            "until_date": until_date,
            "now": timezone.now(),
        },
    )


@login_required
@require_POST
def set_rsvp(
    request: HttpRequest,
    *,
    student_id: UUID,
    lesson_id: UUID,
) -> HttpResponse:
    status = request.POST.get("status", "")
    try:
        set_lesson_response(
            actor=request.user,
            student_id=student_id,
            lesson_id=lesson_id,
            status=status,
            now=timezone.now(),
        )
    except Lesson.DoesNotExist as exc:
        raise Http404("Lesson not found.") from exc
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Ответ сохранён.")

    return redirect(
        f"{reverse('scheduling:student_schedule')}?student={student_id}"
    )


@login_required
def coach_schedule(request: HttpRequest) -> HttpResponse:
    coach = _coach_for_user(request)
    school_date = _parse_date(
        request.GET.get("date"),
        default=get_school_date(timezone.now()),
    )
    lessons = get_coach_schedule(
        coach_id=coach.id,
        from_date=school_date,
        until_date=school_date,
    )
    return render(
        request,
        "scheduling/coach_schedule.html",
        {
            "coach": coach,
            "school_date": school_date,
            "lessons": lessons,
        },
    )


@login_required
def coach_lesson(request: HttpRequest, *, lesson_id: UUID) -> HttpResponse:
    lesson = get_object_or_404(
        Lesson.objects.select_related(
            "group",
            "lesson_type",
            "coach",
            "venue",
        ),
        pk=lesson_id,
    )
    _assert_lesson_actor(request, lesson)

    roster_entries = list(
        LessonRosterEntry.objects.filter(
            lesson=lesson,
            is_active=True,
        )
        .select_related("student")
        .order_by("student__display_name", "student_id")
    )
    student_ids = [entry.student_id for entry in roster_entries]
    responses = {
        response.student_id: response.status
        for response in LessonResponse.objects.filter(
            lesson=lesson,
            student_id__in=student_ids,
        )
    }
    attendances = {
        attendance.student_id: attendance
        for attendance in Attendance.objects.filter(
            lesson=lesson,
            student_id__in=student_ids,
        )
    }
    coverage_by_attendance = {
        coverage.attendance_id: coverage
        for coverage in AttendanceCoverage.objects.filter(
            attendance_id__in=[
                attendance.id for attendance in attendances.values()
            ],
            reversed_at__isnull=True,
        ).select_related(
            "subscription_allowance",
            "one_time_entitlement",
            "makeup_entitlement",
        )
    }

    rows = []
    for entry in roster_entries:
        attendance = attendances.get(entry.student_id)
        rows.append(
            {
                "entry": entry,
                "response_status": responses.get(entry.student_id),
                "attendance": attendance,
                "coverage": (
                    coverage_by_attendance.get(attendance.id)
                    if attendance is not None
                    else None
                ),
            }
        )

    return render(
        request,
        "scheduling/coach_lesson.html",
        {
            "lesson": lesson,
            "rows": rows,
            "now": timezone.now(),
        },
    )


@login_required
@require_POST
def coach_set_attendance(
    request: HttpRequest,
    *,
    lesson_id: UUID,
    student_id: UUID,
) -> HttpResponse:
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    _assert_lesson_actor(request, lesson)
    status = request.POST.get("status", "")

    try:
        set_attendance(
            lesson_id=lesson.id,
            student_id=student_id,
            status=status,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("scheduling:coach_lesson", lesson_id=lesson.id)


@login_required
@require_POST
def coach_mark_expected_present(
    request: HttpRequest,
    *,
    lesson_id: UUID,
) -> HttpResponse:
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    _assert_lesson_actor(request, lesson)
    confirmed = request.POST.get("confirmed") == "1"
    try:
        count = mark_expected_present(
            lesson_id=lesson.id,
            actor=request.user,
            now=timezone.now(),
            confirmed=confirmed,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Отмечено присутствующими: {count}.")
    return redirect("scheduling:coach_lesson", lesson_id=lesson.id)


@login_required
@require_POST
def coach_mark_remaining_absent(
    request: HttpRequest,
    *,
    lesson_id: UUID,
) -> HttpResponse:
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    _assert_lesson_actor(request, lesson)
    try:
        count = mark_remaining_absent(
            lesson_id=lesson.id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Отмечено отсутствующими: {count}.")
    return redirect("scheduling:coach_lesson", lesson_id=lesson.id)


@login_required
@require_POST
def coach_complete_lesson(
    request: HttpRequest,
    *,
    lesson_id: UUID,
) -> HttpResponse:
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    _assert_lesson_actor(request, lesson)
    try:
        complete_lesson(
            lesson_id=lesson.id,
            now=timezone.now(),
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Занятие переведено в COMPLETED.")
    return redirect("scheduling:coach_lesson", lesson_id=lesson.id)


@login_required
@require_POST
def coach_submit_attendance(
    request: HttpRequest,
    *,
    lesson_id: UUID,
) -> HttpResponse:
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    _assert_lesson_actor(request, lesson)
    try:
        submit_attendance(
            lesson_id=lesson.id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Ведомость закрыта.")
    return redirect("scheduling:coach_lesson", lesson_id=lesson.id)
