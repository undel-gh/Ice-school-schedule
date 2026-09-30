from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.permissions import require_permission
from core.time import school_date

from .forms import ManagerMedicalVerifyForm
from .models import AbsenceJustification
from .services import (
    reject_medical_absence,
    revoke_medical_absence,
    verify_medical_absence,
)


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return " ".join(
            message
            for messages_ in exc.message_dict.values()
            for message in messages_
        )
    return " ".join(exc.messages)


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
            "statuses": AbsenceJustification.Status.choices,
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
            messages.error(request, _validation_message(exc))
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
        messages.error(request, _validation_message(exc))
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
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Подтверждённое медицинское основание отозвано.")
    return redirect(
        "attendance_manager:medical_detail",
        justification_id=justification_id,
    )
