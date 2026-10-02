from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from .models import StudentAccess

User = get_user_model()


@dataclass(frozen=True, slots=True)
class StudentNavigationAvailability:
    account_available: bool
    schedule_available: bool


def student_navigation_availability(user: User) -> StudentNavigationAvailability:
    """Resolve student navigation flags with at most one database query."""
    if not getattr(user, "is_authenticated", False):
        return StudentNavigationAvailability(False, False)

    counts = StudentAccess.objects.filter(
        user=user,
        is_active=True,
    ).aggregate(
        account_count=Count("pk"),
        active_student_count=Count(
            "pk",
            filter=Q(student__is_active=True),
        ),
    )
    return StudentNavigationAvailability(
        account_available=counts["account_count"] > 0,
        schedule_available=counts["active_student_count"] > 0,
    )


def student_accesses_for_user(
    user: User,
    *,
    active_students_only: bool = False,
):
    if not getattr(user, "is_authenticated", False):
        return StudentAccess.objects.none()

    accesses = StudentAccess.objects.filter(
        user=user,
        is_active=True,
    )
    if active_students_only:
        accesses = accesses.filter(student__is_active=True)
    return accesses.select_related("student").order_by(
        "-student__is_active",
        "student__display_name",
        "student_id",
    )
