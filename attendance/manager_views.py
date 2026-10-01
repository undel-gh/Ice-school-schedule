from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Student
from core.permissions import require_permission
from core.presentation import localized_choices, validation_message
from core.time import school_date

from subscriptions.models import AttendanceCoverage
from subscriptions.services import rebind_attendance_coverage

from .forms import (
    ManagerAttendanceCoverageRebindForm,
    ManagerMedicalVerifyForm,
)
from .models import AbsenceJustification, Attendance
from .selectors import (
    available_attendance_coverage_targets,
    manager_attendance_coverage_report,
)
from .services import (
    reject_medical_absence,
    recover_attendance_coverage,
    revoke_medical_absence,
    verify_medical_absence,
)

MAX_MANAGER_COVERAGE_REPORT_RANGE_DAYS = 366


def _parse_report_date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404("Invalid date.") from exc


@login_required
def manager_coverage_report(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_attendancecoverage",
        "Attendance coverage view permission is required.",
    )

    today = school_date(timezone.now())
    from_date = _parse_report_date(
        request.GET.get("from"),
        default=today - timedelta(days=90),
    )
    until_date = _parse_report_date(
        request.GET.get("until"),
        default=today,
    )
    if until_date < from_date:
        raise Http404("Invalid date range.")
    if (
        until_date - from_date
    ).days > MAX_MANAGER_COVERAGE_REPORT_RANGE_DAYS:
        raise Http404("Date range is too large.")

    coverage_state = request.GET.get("coverage", "uncovered")
    if coverage_state not in {"uncovered", "covered", "all"}:
        raise Http404("Invalid coverage state.")

    student_id = None
    raw_student = request.GET.get("student")
    if raw_student:
        try:
            student_id = UUID(raw_student)
        except ValueError as exc:
            raise Http404("Invalid student.") from exc
        if not Student.objects.filter(pk=student_id).exists():
            raise Http404("Student not found.")

    rows = manager_attendance_coverage_report(
        coverage_state=coverage_state,
        student_id=student_id,
        from_date=from_date,
        until_date=until_date,
    )
    students = Student.objects.order_by("display_name", "id")

    return render(
        request,
        "attendance/manager_coverage_report.html",
        {
            "rows": rows,
            "students": students,
            "selected_student_id": (
                str(student_id) if student_id is not None else ""
            ),
            "selected_coverage_state": coverage_state,
            "from_date": from_date,
            "until_date": until_date,
        },
    )


@login_required
def manager_coverage_detail(
    request: HttpRequest,
    *,
    attendance_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_attendancecoverage",
        "Attendance coverage view permission is required.",
    )
    attendance = get_object_or_404(
        Attendance.objects.select_related(
            "student",
            "lesson__lesson_type",
            "lesson__group",
        ),
        pk=attendance_id,
    )
    coverage = (
        AttendanceCoverage.objects.filter(
            attendance=attendance,
            reversed_at__isnull=True,
        )
        .select_related(
            "subscription_allowance__subscription",
            "one_time_entitlement",
            "makeup_entitlement",
        )
        .first()
    )
    targets = (
        available_attendance_coverage_targets(
            attendance=attendance,
            current_coverage=coverage,
        )
        if attendance.status == Attendance.Status.PRESENT
        else ()
    )
    rebind_form = ManagerAttendanceCoverageRebindForm(targets=targets)

    return render(
        request,
        "attendance/manager_coverage_detail.html",
        {
            "attendance": attendance,
            "coverage": coverage,
            "targets": targets,
            "rebind_form": rebind_form,
            "lesson_is_completed": (
                attendance.lesson.status == attendance.lesson.Status.COMPLETED
            ),
        },
    )


@login_required
@require_POST
def manager_coverage_rebind(
    request: HttpRequest,
    *,
    attendance_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_attendancecoverage",
        "Attendance coverage change permission is required.",
    )
    attendance = get_object_or_404(
        Attendance.objects.select_related("lesson__lesson_type"),
        pk=attendance_id,
    )
    coverage = (
        AttendanceCoverage.objects.filter(
            attendance=attendance,
            reversed_at__isnull=True,
        )
        .select_related(
            "subscription_allowance__subscription",
            "one_time_entitlement",
            "makeup_entitlement",
        )
        .first()
    )
    targets = available_attendance_coverage_targets(
        attendance=attendance,
        current_coverage=coverage,
    )
    form = ManagerAttendanceCoverageRebindForm(
        request.POST,
        targets=targets,
    )
    if form.is_valid():
        try:
            rebind_attendance_coverage(
                attendance_id=attendance.id,
                actor=request.user,
                **form.target_kwargs(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Источник покрытия изменён.")
    else:
        messages.error(
            request,
            "Выберите доступный источник покрытия.",
        )
    return redirect(
        "attendance_manager:coverage_detail",
        attendance_id=attendance.id,
    )


@login_required
@require_POST
def manager_coverage_recover(
    request: HttpRequest,
    *,
    attendance_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.change_attendancecoverage",
        "Attendance coverage change permission is required.",
    )
    get_object_or_404(Attendance, pk=attendance_id)
    try:
        recover_attendance_coverage(
            attendance_id=attendance_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(
            request,
            "Покрытие посещения восстановлено.",
        )
    return redirect(
        "attendance_manager:coverage_detail",
        attendance_id=attendance_id,
    )


@login_required
def manager_medical_absences(request: HttpRequest) -> HttpResponse:
    require_permission(
        request.user,
        "attendance.view_absencejustification",
        "Medical justification view permission is required.",
    )
    justifications = (
        AbsenceJustification.objects.filter(
            type=AbsenceJustification.Type.MEDICAL,
        )
        .select_related("student", "lesson__lesson_type", "declared_by", "reviewed_by")
        .order_by("-declared_at", "id")
    )
    status = request.GET.get("status")
    if status in AbsenceJustification.Status.values:
        justifications = justifications.filter(status=status)
    return render(
        request,
        "attendance/manager_medical_absences.html",
        {
            "justifications": justifications[:300],
            "statuses": localized_choices(
                "medical_status",
                AbsenceJustification.Status.choices,
            ),
            "selected_status": status or "",
        },
    )


@login_required
def manager_medical_detail(
    request: HttpRequest,
    *,
    justification_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "attendance.view_absencejustification",
        "Medical justification view permission is required.",
    )
    justification = get_object_or_404(
        AbsenceJustification.objects.select_related(
            "student", "lesson__lesson_type", "lesson__group",
            "declared_by", "reviewed_by", "revoked_by",
        ),
        pk=justification_id,
        type=AbsenceJustification.Type.MEDICAL,
    )
    return render(
        request,
        "attendance/manager_medical_detail.html",
        {
            "justification": justification,
            "verify_form": ManagerMedicalVerifyForm(
                initial={"valid_until": school_date(timezone.now()) + timedelta(days=60)}
            ),
        },
    )


@login_required
@require_POST
def manager_medical_verify(
    request: HttpRequest,
    *,
    justification_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "attendance.change_absencejustification",
        "Medical absence review permission is required.",
    )
    get_object_or_404(
        AbsenceJustification,
        pk=justification_id,
        type=AbsenceJustification.Type.MEDICAL,
    )
    form = ManagerMedicalVerifyForm(request.POST)
    if form.is_valid():
        try:
            verify_medical_absence(
                justification_id=justification_id,
                actor=request.user,
                valid_until=form.cleaned_data["valid_until"],
                now=timezone.now(),
            )
        except ValidationError as exc:
            messages.error(request, validation_message(exc))
        else:
            messages.success(request, "Медицинское основание подтверждено.")
    else:
        messages.error(request, "Укажите срок действия отработки.")
    return redirect(
        "attendance_manager:medical_detail",
        justification_id=justification_id,
    )


@login_required
@require_POST
def manager_medical_reject(
    request: HttpRequest,
    *,
    justification_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "attendance.change_absencejustification",
        "Medical absence review permission is required.",
    )
    get_object_or_404(
        AbsenceJustification,
        pk=justification_id,
        type=AbsenceJustification.Type.MEDICAL,
    )
    try:
        reject_medical_absence(
            justification_id=justification_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Медицинское основание отклонено.")
    return redirect(
        "attendance_manager:medical_detail",
        justification_id=justification_id,
    )


@login_required
@require_POST
def manager_medical_revoke(
    request: HttpRequest,
    *,
    justification_id: UUID,
) -> HttpResponse:
    require_permission(
        request.user,
        "attendance.change_absencejustification",
        "Medical absence review permission is required.",
    )
    get_object_or_404(
        AbsenceJustification,
        pk=justification_id,
        type=AbsenceJustification.Type.MEDICAL,
    )
    try:
        revoke_medical_absence(
            justification_id=justification_id,
            actor=request.user,
            now=timezone.now(),
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        messages.success(request, "Подтверждённое медицинское основание отозвано.")
    return redirect(
        "attendance_manager:medical_detail",
        justification_id=justification_id,
    )
