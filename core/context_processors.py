from __future__ import annotations

from accounts.selectors import student_accesses_for_user

from .permissions import has_manager_operations_access


def manager_operations(request):
    return {
        "manager_operations_available": has_manager_operations_access(
            request.user
        )
    }


def student_navigation(request):
    accesses = student_accesses_for_user(request.user)
    if not accesses.exists():
        return {
            "student_account_available": False,
            "student_schedule_available": False,
        }
    return {
        "student_account_available": True,
        "student_schedule_available": accesses.filter(
            student__is_active=True
        ).exists(),
    }
