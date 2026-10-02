from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.permissions import has_manager_operations_assignment


User = get_user_model()

MFA_PREAUTH_SESSION_KEY = "mfa_preauth"
MFA_NEXT_SESSION_KEY = "mfa_next"
MFA_SETUP_DEVICE_SESSION_KEY = "mfa_setup_device_id"
MFA_RECOVERY_CODES_SESSION_KEY = "mfa_recovery_codes"


@dataclass(frozen=True, slots=True)
class MFAIdentity:
    user: Any
    backend: str | None
    next_url: str | None
    preauthenticated: bool


def mfa_required_for_user(user) -> bool:
    if user is None:
        return False
    return bool(
        getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
        or has_manager_operations_assignment(user)
    )


def has_confirmed_totp(user) -> bool:
    return TOTPDevice.objects.filter(
        user=user,
        confirmed=True,
    ).exists()


def has_confirmed_mfa_device(user) -> bool:
    return user_has_device(user, confirmed=True)


def begin_mfa_preauth(
    request,
    *,
    user,
    backend: str,
    next_url: str | None,
) -> None:
    request.session.cycle_key()
    request.session[MFA_PREAUTH_SESSION_KEY] = {
        "user_id": str(user.pk),
        "backend": backend,
        "issued_at": timezone.now().timestamp(),
        "auth_hash": user.get_session_auth_hash(),
        "next_url": next_url or "",
    }
    if next_url:
        request.session[MFA_NEXT_SESSION_KEY] = next_url
    request.session.modified = True


def clear_mfa_transient_session(request) -> None:
    request.session.pop(MFA_PREAUTH_SESSION_KEY, None)
    request.session.pop(MFA_SETUP_DEVICE_SESSION_KEY, None)
    request.session.modified = True


def remember_mfa_next(request, value: str) -> None:
    request.session[MFA_NEXT_SESSION_KEY] = value
    request.session.modified = True


def _safe_next_url(request, value: str | None) -> str | None:
    if not value:
        return None
    if not url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return None
    return value


def pop_mfa_next(request, *, fallback: str) -> str:
    preauth = request.session.get(MFA_PREAUTH_SESSION_KEY)
    candidate = None
    if isinstance(preauth, dict):
        candidate = preauth.get("next_url")
    if not candidate:
        candidate = request.session.pop(MFA_NEXT_SESSION_KEY, None)
    safe = _safe_next_url(request, str(candidate or ""))
    return safe or fallback


def resolve_mfa_identity(request) -> MFAIdentity | None:
    if request.user.is_authenticated:
        return MFAIdentity(
            user=request.user,
            backend=None,
            next_url=request.session.get(MFA_NEXT_SESSION_KEY),
            preauthenticated=False,
        )

    data = request.session.get(MFA_PREAUTH_SESSION_KEY)
    if not isinstance(data, dict):
        return None

    ttl = int(getattr(settings, "MFA_PREAUTH_TTL_SECONDS", 300))
    try:
        age = timezone.now().timestamp() - float(data.get("issued_at"))
    except (TypeError, ValueError):
        age = ttl + 1
    if age < 0 or age > ttl:
        clear_mfa_transient_session(request)
        return None

    try:
        user = User.objects.get(pk=data.get("user_id"), is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        clear_mfa_transient_session(request)
        return None

    if not mfa_required_for_user(user):
        clear_mfa_transient_session(request)
        return None

    auth_hash = str(data.get("auth_hash") or "")
    if not auth_hash or not constant_time_compare(
        auth_hash,
        user.get_session_auth_hash(),
    ):
        clear_mfa_transient_session(request)
        return None

    backend = str(data.get("backend") or "")
    if not backend:
        clear_mfa_transient_session(request)
        return None

    return MFAIdentity(
        user=user,
        backend=backend,
        next_url=_safe_next_url(
            request,
            str(data.get("next_url") or ""),
        ),
        preauthenticated=True,
    )
