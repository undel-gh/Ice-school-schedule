from __future__ import annotations

from accounts.selectors import student_navigation_availability

from .permissions import has_manager_operations_access


def manager_operations(request):
    user = getattr(request, "user", None)
    return {
        "manager_operations_available": (
            has_manager_operations_access(user) if user is not None else False
        )
    }


def student_navigation(request):
    user = getattr(request, "user", None)
    if user is None:
        return {
            "student_account_available": False,
            "student_schedule_available": False,
        }

    cache_attr = "_student_navigation_availability"
    availability = getattr(request, cache_attr, None)
    if availability is None:
        availability = student_navigation_availability(user)
        setattr(request, cache_attr, availability)
    return {
        "student_account_available": availability.account_available,
        "student_schedule_available": availability.schedule_available,
    }
