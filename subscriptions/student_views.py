from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.selectors import student_accesses_for_user
from core.time import school_date

from .student_account import (
    student_account_snapshot,
    student_attendance_history,
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
        now=now,
    )
    history_page = Paginator(
        student_attendance_history(student_id=selected.id),
        25,
    ).get_page(request.GET.get("page"))

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
