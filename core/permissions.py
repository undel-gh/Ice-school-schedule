from __future__ import annotations

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db.models import Q


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
    "accounts.change_externalidentity",
    "accounts.view_accountinvitation",
    "accounts.add_accountinvitation",
    "accounts.change_accountinvitation",
    "scheduling.view_traininggroup",
    "scheduling.add_traininggroup",
    "scheduling.change_traininggroup",
    "scheduling.view_groupmembership",
    "scheduling.add_groupmembership",
    "scheduling.change_groupmembership",
    "subscriptions.view_subscriptionperiodscheme",
    "subscriptions.add_subscriptionperiodscheme",
    "subscriptions.change_subscriptionperiodscheme",
    "subscriptions.view_subscriptionplan",
    "subscriptions.add_subscriptionplan",
    "subscriptions.change_subscriptionplan",
    "subscriptions.view_absencecompensationpolicy",
    "subscriptions.add_absencecompensationpolicy",
    "subscriptions.change_absencecompensationpolicy",
    "subscriptions.view_absencecompensationpolicyaction",
    "subscriptions.add_absencecompensationpolicyaction",
    "subscriptions.change_absencecompensationpolicyaction",
    "subscriptions.view_absencecompensationpolicywindow",
    "subscriptions.add_absencecompensationpolicywindow",
    "subscriptions.change_absencecompensationpolicywindow",
    "subscriptions.view_subscription",
    "subscriptions.view_attendancecoverage",
    "subscriptions.change_attendancecoverage",
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


def has_manager_operations_assignment(actor) -> bool:
    """
    Return whether manager-operation permissions are assigned to the User,
    independent of is_active. Identity recovery uses this to ensure that
    deactivating a privileged account never turns it into an external-auth
    eligible account merely because Django permission backends stop reporting
    permissions for inactive users.
    """
    if actor is None:
        return False
    if getattr(actor, "is_superuser", False):
        return True

    permission_query = Q()
    for permission in MANAGER_OPERATION_PERMISSIONS:
        app_label, codename = permission.split(".", 1)
        permission_query |= Q(
            content_type__app_label=app_label,
            codename=codename,
        )

    if not permission_query:
        return False
    if actor.user_permissions.filter(permission_query).exists():
        return True
    manager_permissions = Permission.objects.filter(permission_query)
    return actor.groups.filter(permissions__in=manager_permissions).exists()


def has_manager_operations_access(actor) -> bool:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if not getattr(actor, "is_active", False):
        return False
    if actor.is_superuser:
        return True

    cache_attr = "_manager_operations_assignment_request_cache"
    if hasattr(actor, cache_attr):
        return bool(getattr(actor, cache_attr))

    # With the configured Django ModelBackend, active-user access to these
    # global permissions is equivalent to direct/group assignment. Cache the
    # result on the request-scoped User instance so MFA middleware and context
    # processors do not repeat permission/group queries.
    assigned = has_manager_operations_assignment(actor)
    setattr(actor, cache_attr, assigned)
    return assigned


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
