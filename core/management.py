from __future__ import annotations

from functools import wraps

from django.contrib.auth import get_user_model
from django.core.exceptions import (
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
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


def command_errors(func):
    """Render expected domain failures as concise management CommandError."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except CommandError:
            raise
        except ValidationError as exc:
            message = " ".join(exc.messages)
            raise CommandError(message) from exc
        except PermissionDenied as exc:
            raise CommandError(str(exc)) from exc
        except ObjectDoesNotExist as exc:
            raise CommandError(str(exc)) from exc

    return wrapper
