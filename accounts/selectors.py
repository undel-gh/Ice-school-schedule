from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import StudentAccess

User = get_user_model()


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
