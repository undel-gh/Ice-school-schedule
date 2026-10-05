from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.permissions import require_student_access
from core.presentation import validation_message

from .models import AbsenceJustification
from .services import declare_medical_absence


@login_required
@require_POST
def declare_medical_absence_view(
    request: HttpRequest,
    *,
    student_id: UUID,
    lesson_id: UUID,
) -> HttpResponse:
    # Keep authorization at the HTTP boundary as well as in the application
    # service. This prevents a future view refactor from turning the service
    # call into an accidental access-control boundary bypass.
    require_student_access(
        actor=request.user,
        student_id=student_id,
    )

    try:
        justification = declare_medical_absence(
            student_id=student_id,
            lesson_id=lesson_id,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
    else:
        if justification.status == AbsenceJustification.Status.VERIFIED:
            messages.info(
                request,
                "Медицинское основание уже подтверждено.",
            )
        else:
            messages.success(
                request,
                "Медицинское основание зарегистрировано и ожидает проверки менеджером.",
            )

    return redirect(
        f"{reverse('scheduling:student_schedule')}?student={student_id}"
    )
