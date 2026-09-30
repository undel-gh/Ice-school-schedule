from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.models import Student
from core.permissions import require_permission
from core.time import school_date
from subscriptions.selectors import manager_subscription_report


def _parse_date(value: str | None, *, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Http404("Invalid date.") from exc


@login_required
def manager_subscription_report_view(
    request: HttpRequest,
) -> HttpResponse:
    require_permission(
        request.user,
        "subscriptions.view_subscription",
        "Manager subscription report permission is required.",
    )

    today = school_date(timezone.now())
    default_from = today.replace(day=1)
    if default_from.month == 12:
        next_month = date(default_from.year + 1, 1, 1)
    else:
        next_month = date(
            default_from.year,
            default_from.month + 1,
            1,
        )
    default_until = next_month - timedelta(days=1)

    from_date = _parse_date(
        request.GET.get("from"),
        default=default_from,
    )
    until_date = _parse_date(
        request.GET.get("until"),
        default=default_until,
    )
    if until_date < from_date:
        raise Http404("Invalid date range.")

    student_id = None
    raw_student = request.GET.get("student")
    if raw_student:
        try:
            student_id = UUID(raw_student)
        except ValueError as exc:
            raise Http404("Invalid student.") from exc
        if not Student.objects.filter(pk=student_id).exists():
            raise Http404("Student not found.")

    rows = manager_subscription_report(
        as_of=today,
        student_id=student_id,
        from_date=from_date,
        until_date=until_date,
    )
    students = Student.objects.filter(is_active=True).order_by(
        "display_name",
        "id",
    )

    return render(
        request,
        "subscriptions/manager_subscription_report.html",
        {
            "rows": rows,
            "students": students,
            "selected_student_id": (
                str(student_id) if student_id is not None else ""
            ),
            "from_date": from_date,
            "until_date": until_date,
            "as_of": today,
        },
    )
