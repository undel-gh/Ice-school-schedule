from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

User = get_user_model()


def resolve_actor(username: str):
    try:
        actor = User.objects.get(username=username, is_active=True)
    except User.DoesNotExist as exc:
        raise CommandError(
            f"Active user '{username}' was not found."
        ) from exc
    return actor
