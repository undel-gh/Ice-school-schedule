from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .mfa import (
    has_confirmed_totp,
    mfa_required_for_user,
    remember_mfa_next,
)


class PrivilegedMFAMiddleware:
    """
    Require an OTP-verified session for privileged web accounts.

    This also upgrades sessions that existed before MFA was deployed: a valid
    password session alone is not enough to reach manager/staff/admin pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if (
            user.is_authenticated
            and mfa_required_for_user(user)
            and not getattr(user, "is_verified", lambda: False)()
            and not self._is_exempt(request)
        ):
            remember_mfa_next(request, request.get_full_path())
            if has_confirmed_totp(user):
                return redirect("mfa:challenge")
            return redirect("mfa:setup")
        return self.get_response(request)

    @staticmethod
    def _is_exempt(request) -> bool:
        path = request.path_info
        allowed = {
            reverse("login"),
            reverse("logout"),
            reverse("mfa:challenge"),
            reverse("mfa:setup"),
            reverse("mfa:setup_qr"),
        }
        if path in allowed:
            return True

        static_url = getattr(settings, "STATIC_URL", "")
        if static_url:
            static_path = "/" + static_url.lstrip("/")
            if path.startswith(static_path):
                return True
        return False
