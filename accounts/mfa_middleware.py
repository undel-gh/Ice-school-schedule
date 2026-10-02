from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as django_logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from .mfa import mfa_required_for_user


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

        # Django Admin has its own password LoginView. Route it through the
        # single MFA-aware local login flow so a staff session is never
        # established by an alternate password-only endpoint.
        if (
            not user.is_authenticated
            and request.path_info == reverse("admin:login")
        ):
            query = urlencode({"next": reverse("admin:index")})
            return redirect(f"{reverse('login')}?{query}")

        if (
            user.is_authenticated
            and mfa_required_for_user(user)
            and not getattr(user, "is_verified", lambda: False)()
            and not self._is_exempt(request)
        ):
            target = request.get_full_path()
            has_password = user.has_usable_password()
            django_logout(request)
            if not has_password:
                messages.error(
                    request,
                    "Привилегированный аккаунт требует локальный пароль как "
                    "первый фактор. Обратитесь к администратору.",
                )
                return redirect("login")
            messages.info(
                request,
                "Для привилегированного доступа войдите локальным паролем "
                "и подтвердите MFA.",
            )
            query = urlencode({"next": target})
            return redirect(f"{reverse('login')}?{query}")
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
