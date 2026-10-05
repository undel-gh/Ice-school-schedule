from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.selectors import student_accesses_for_user
from attendance.models import AbsenceJustification
from core.time import school_date

from .student_account import (
    student_account_snapshot,
    student_attendance_history,
)


def _decorate_medical_history(history_page, *, student_id) -> None:
    lesson_ids = [
        attendance.lesson_id
        for attendance in history_page.object_list
        if attendance.status == attendance.Status.ABSENT
    ]
    if not lesson_ids:
        return

    latest_by_lesson = {}
    justifications = (
        AbsenceJustification.objects.filter(
            student_id=student_id,
            lesson_id__in=lesson_ids,
            type=AbsenceJustification.Type.MEDICAL,
        )
        .order_by("lesson_id", "-declared_at", "-id")
    )
    for justification in justifications:
        latest_by_lesson.setdefault(justification.lesson_id, justification)

    for attendance in history_page.object_list:
        if attendance.status != attendance.Status.ABSENT:
            continue
        justification = latest_by_lesson.get(attendance.lesson_id)
        attendance.account_medical_justification = justification
        attendance.account_can_declare_medical_absence = (
            justification is None
            or (
                justification.status == AbsenceJustification.Status.REVOKED
                and justification.revocation_reason
                == AbsenceJustification.RevocationReason.ATTENDANCE_CORRECTION
            )
        )


@login_required
def account(request: HttpRequest) -> HttpResponse:
    accesses = list(student_accesses_for_user(request.user))
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

    now = timezone.now()
    snapshot = student_account_snapshot(
        student_id=selected.id,
        as_of=school_date(now),
    )
    history_page = Paginator(
        student_attendance_history(student_id=selected.id),
        25,
    ).get_page(request.GET.get("page"))
    _decorate_medical_history(
        history_page,
        student_id=selected.id,
    )

    return render(
        request,
        "subscriptions/student_account.html",
        {
            "students": tuple(access.student for access in accesses),
            "selected_student": selected,
            "account": snapshot,
            "history_page": history_page,
        },
    )
