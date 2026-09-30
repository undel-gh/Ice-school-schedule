from __future__ import annotations

from .permissions import has_manager_operations_access


def manager_operations(request):
    return {
        "manager_operations_available": has_manager_operations_access(
            request.user
        )
    }
