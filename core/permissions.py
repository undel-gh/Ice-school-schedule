from __future__ import annotations

from django.core.exceptions import PermissionDenied


def require_permission(actor, permission: str, message: str) -> None:
    if actor is not None and (
        actor.is_superuser or actor.has_perm(permission)
    ):
        return
    raise PermissionDenied(message)


def require_student_access(*, actor, student_id) -> None:
    from accounts.models import StudentAccess

    if StudentAccess.objects.filter(
        user=actor,
        student_id=student_id,
        is_active=True,
    ).exists():
        return
    raise PermissionDenied(
        "Active SELF or GUARDIAN access to this student is required."
    )


def require_lesson_coach_or_permission(
    *,
    actor,
    lesson,
    permission: str,
    message: str,
) -> None:
    if actor.is_superuser or actor.has_perm(permission):
        return
    if lesson.coach.user_id == actor.id and lesson.coach.is_active:
        return
    raise PermissionDenied(message)
