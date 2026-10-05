from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from django.db.models import OuterRef, Q, Subquery

from .models import AbsenceJustification


_REDECLARABLE_MEDICAL_STATES = frozenset(
    {
        (
            AbsenceJustification.Status.REVOKED,
            AbsenceJustification.RevocationReason.ATTENDANCE_CORRECTION,
        ),
    }
)


def medical_declaration_is_available(
    justification: AbsenceJustification | None,
) -> bool:
    """Return whether a new medical declaration may be started."""
    if justification is None:
        return True
    return (
        justification.status,
        justification.revocation_reason,
    ) in _REDECLARABLE_MEDICAL_STATES


def latest_medical_justifications_by_lesson(
    *,
    student_id: UUID,
    lesson_ids: Iterable[UUID],
) -> dict[UUID, AbsenceJustification]:
    """Return the latest medical justification for each requested lesson."""
    requested_ids = tuple(lesson_ids)
    if not requested_ids:
        return {}

    latest_by_lesson: dict[UUID, AbsenceJustification] = {}
    rows = AbsenceJustification.objects.filter(
        student_id=student_id,
        lesson_id__in=requested_ids,
        type=AbsenceJustification.Type.MEDICAL,
    ).order_by("lesson_id", "-declared_at", "-id")
    for justification in rows:
        latest_by_lesson.setdefault(
            justification.lesson_id,
            justification,
        )
    return latest_by_lesson


def _medical_declaration_available_q(
    *,
    status_field: str,
    revocation_reason_field: str,
) -> Q:
    allowed = Q(**{f"{status_field}__isnull": True})
    for status, revocation_reason in _REDECLARABLE_MEDICAL_STATES:
        state = {status_field: status}
        if revocation_reason is None:
            state[f"{revocation_reason_field}__isnull"] = True
        else:
            state[revocation_reason_field] = revocation_reason
        allowed |= Q(**state)
    return allowed


def filter_medical_declaration_candidates(queryset):
    """Keep rows whose latest medical state permits a new declaration.

    The queryset must expose ``student_id`` and ``lesson_id`` fields. The
    annotations intentionally use the same redeclarable-state definition as
    :func:`medical_declaration_is_available` so UI/query decisions cannot
    drift from the domain rule.
    """
    latest = AbsenceJustification.objects.filter(
        student_id=OuterRef("student_id"),
        lesson_id=OuterRef("lesson_id"),
        type=AbsenceJustification.Type.MEDICAL,
    ).order_by("-declared_at", "-id")

    status_field = "medical_declaration_latest_status"
    reason_field = "medical_declaration_latest_revocation_reason"
    annotated = queryset.annotate(
        **{
            status_field: Subquery(latest.values("status")[:1]),
            reason_field: Subquery(latest.values("revocation_reason")[:1]),
        }
    )
    return annotated.filter(
        _medical_declaration_available_q(
            status_field=status_field,
            revocation_reason_field=reason_field,
        )
    )
