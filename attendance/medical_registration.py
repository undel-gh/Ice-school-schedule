from __future__ import annotations

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery

from audit.services import record_event
from core.permissions import require_permission

from .models import AbsenceJustification, Attendance


def _terminal_redeclaration_is_allowed(
    justification: AbsenceJustification | None,
) -> bool:
    return bool(
        justification is not None
        and justification.status == AbsenceJustification.Status.REVOKED
        and justification.revocation_reason
        == AbsenceJustification.RevocationReason.ATTENDANCE_CORRECTION
    )


@transaction.atomic
def declare_medical_absence_by_manager(
    *,
    attendance_id: UUID,
    actor,
) -> AbsenceJustification:
    require_permission(
        actor,
        "attendance.add_absencejustification",
        "Medical absence registration permission is required.",
    )

    attendance = Attendance.objects.select_for_update().get(pk=attendance_id)
    if attendance.status != Attendance.Status.ABSENT:
        raise ValidationError(
            {
                "attendance": (
                    "Medical absence can only be declared for an "
                    "Attendance=ABSENT record."
                )
            }
        )

    active = (
        AbsenceJustification.objects.select_for_update()
        .filter(
            student_id=attendance.student_id,
            lesson_id=attendance.lesson_id,
            type=AbsenceJustification.Type.MEDICAL,
            status__in=[
                AbsenceJustification.Status.PENDING,
                AbsenceJustification.Status.VERIFIED,
            ],
        )
        .order_by("-declared_at", "-id")
        .first()
    )
    if active is not None:
        return active

    latest = (
        AbsenceJustification.objects.select_for_update()
        .filter(
            student_id=attendance.student_id,
            lesson_id=attendance.lesson_id,
            type=AbsenceJustification.Type.MEDICAL,
        )
        .order_by("-declared_at", "-id")
        .first()
    )
    if latest is not None and not _terminal_redeclaration_is_allowed(latest):
        raise ValidationError(
            {
                "justification": (
                    "A terminal medical justification already exists for "
                    "this absence and cannot be redeclared automatically."
                )
            }
        )

    justification = AbsenceJustification.objects.create(
        student_id=attendance.student_id,
        lesson_id=attendance.lesson_id,
        type=AbsenceJustification.Type.MEDICAL,
        status=AbsenceJustification.Status.PENDING,
        verification_method=AbsenceJustification.VerificationMethod.IN_PERSON,
        declared_by=actor,
    )
    record_event(
        event_type=(
            "AbsenceJustificationRedeclared"
            if latest is not None
            else "AbsenceJustificationDeclared"
        ),
        actor=actor,
        aggregate_type="AbsenceJustification",
        aggregate_id=justification.id,
        payload={
            "student_id": str(attendance.student_id),
            "lesson_id": str(attendance.lesson_id),
            "type": justification.type,
            "source": "manager_web",
        },
    )
    return justification


def manager_medical_registration_candidates(*, search: str = ""):
    medical = AbsenceJustification.objects.filter(
        student_id=OuterRef("student_id"),
        lesson_id=OuterRef("lesson_id"),
        type=AbsenceJustification.Type.MEDICAL,
    )
    active_medical = medical.filter(
        status__in=[
            AbsenceJustification.Status.PENDING,
            AbsenceJustification.Status.VERIFIED,
        ]
    )
    latest_medical = medical.order_by("-declared_at", "-id")

    rows = (
        Attendance.objects.filter(status=Attendance.Status.ABSENT)
        .annotate(
            registration_has_active_medical=Exists(active_medical),
            registration_latest_medical_status=Subquery(
                latest_medical.values("status")[:1]
            ),
            registration_latest_revocation_reason=Subquery(
                latest_medical.values("revocation_reason")[:1]
            ),
        )
        .filter(registration_has_active_medical=False)
        .filter(
            Q(registration_latest_medical_status__isnull=True)
            | Q(
                registration_latest_medical_status=(
                    AbsenceJustification.Status.REVOKED
                ),
                registration_latest_revocation_reason=(
                    AbsenceJustification.RevocationReason.ATTENDANCE_CORRECTION
                ),
            )
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
