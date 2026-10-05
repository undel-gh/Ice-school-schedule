from __future__ import annotations

from django.db.models import Q

from .medical_policy import filter_medical_declaration_candidates
from .models import Attendance
from .services import declare_medical_absence_by_manager


def manager_medical_registration_candidates(*, search: str = ""):
    rows = (
        filter_medical_declaration_candidates(
            Attendance.objects.filter(status=Attendance.Status.ABSENT)
        )
        .select_related(
            "student",
            "lesson__lesson_type",
            "lesson__group",
            "lesson__coach",
            "lesson__venue",
        )
        .order_by("-lesson__starts_at", "student__display_name", "id")
    )

    query = search.strip()
    if query:
        rows = rows.filter(
            Q(student__display_name__icontains=query)
            | Q(lesson__lesson_type__name__icontains=query)
            | Q(lesson__group__name__icontains=query)
            | Q(lesson__venue__name__icontains=query)
        )
    return rows
