from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.permissions import require_permission
from core.presentation import validation_message

from .medical_registration import (
    declare_medical_absence_by_manager,
    manager_medical_registration_candidates,
)
from .models import Attendance


def _require_registration_permissions(request: HttpRequest) -> None:
    require_permission(
        request.user,
        "attendance.view_absencejustification",
        "Medical justification view permission is required.",
    )
    require_permission(
        request.user,
        "attendance.add_absencejustification",
        "Medical absence registration permission is required.",
    )


@login_required
def manager_medical_registration(request: HttpRequest) -> HttpResponse:
    _require_registration_permissions(request)
    search = request.GET.get("q", "").strip()
    page_obj = Paginator(
        manager_medical_registration_candidates(search=search),
        50,
    ).get_page(request.GET.get("page"))
    return render(
        request,
        "attendance/manager_medical_registration.html",
        {
            "page_obj": page_obj,
            "search": search,
        },
    )


@login_required
@require_POST
def manager_medical_register_absence(
    request: HttpRequest,
    *,
    attendance_id: UUID,
) -> HttpResponse:
    _require_registration_permissions(request)
    get_object_or_404(Attendance, pk=attendance_id)
    try:
        justification = declare_medical_absence_by_manager(
            attendance_id=attendance_id,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
        return redirect("attendance_manager:medical_register")

    messages.success(
        request,
        "Медицинское основание зарегистрировано и ожидает проверки.",
    )
    return redirect(
        "attendance_manager:medical_detail",
        justification_id=justification.id,
    )
