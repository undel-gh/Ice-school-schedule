from __future__ import annotations

from django.core.exceptions import PermissionDenied


MANAGER_OPERATION_PERMISSIONS = (
    "accounts.view_student",
    "accounts.add_student",
    "accounts.change_student",
    "accounts.view_studentaccess",
    "accounts.add_studentaccess",
    "accounts.change_studentaccess",
    "accounts.view_coachprofile",
    "accounts.add_coachprofile",
    "accounts.change_coachprofile",
    "scheduling.view_traininggroup",
    "scheduling.add_traininggroup",
    "scheduling.change_traininggroup",
    "scheduling.view_groupmembership",
    "scheduling.add_groupmembership",
    "scheduling.change_groupmembership",
    "subscriptions.view_subscription",
    "subscriptions.view_absencecompensationcase",
    "subscriptions.add_absencecompensationcase",
    "subscriptions.change_absencecompensationcase",
    "subscriptions.view_onetimeentitlement",
    "subscriptions.add_onetimeentitlement",
    "subscriptions.change_onetimeentitlement",
    "subscriptions.add_makeupentitlement",
    "subscriptions.add_absencecompensationactiongrant",
    "subscriptions.change_absencecompensationactiongrant",
    "scheduling.view_scheduletemplate",
    "scheduling.add_scheduletemplate",
    "scheduling.change_scheduletemplate",
    "scheduling.view_lesson",
    "scheduling.change_lesson",
    "attendance.view_absencejustification",
    "attendance.change_absencejustification",
    "audit.view_auditevent",
)


def has_manager_operations_access(actor) -> bool:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    return actor.is_superuser or any(
        actor.has_perm(permission)
        for permission in MANAGER_OPERATION_PERMISSIONS
    )


def require_manager_operations_access(actor) -> None:
    if has_manager_operations_access(actor):
        return
    raise PermissionDenied("Manager operations permission is required.")


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
