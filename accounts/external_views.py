from __future__ import annotations

import secrets
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.presentation import validation_message

from .external_auth import (
    build_authorization_url,
    configured_providers,
    exchange_authorization_code,
    generate_oauth_state,
    generate_pkce_verifier,
    provider_is_configured,
)
from .models import AccountInvitation, ExternalIdentity
from .onboarding import (
    accept_account_invitation_for_existing_user,
    authenticate_external_identity,
    link_external_identity,
    resolve_invitation_token,
)


FLOW_SESSION_KEY = "external_auth_flow"
INVITATION_SESSION_KEY = "external_auth_invitation_id"


def _callback_uri(request, provider: str) -> str:
    return request.build_absolute_uri(
        reverse("external_auth:callback", kwargs={"provider": provider})
    )


def _start_flow(request, *, provider: str, mode: str, invitation_id=None):
    if provider not in ExternalIdentity.Provider.values:
        raise ValidationError({"provider": "Unsupported external identity provider."})
    if not provider_is_configured(provider):
        raise ValidationError({"provider": "External identity provider is not configured."})

    state = generate_oauth_state()
    verifier = generate_pkce_verifier()
    redirect_uri = _callback_uri(request, provider)
    flow = {
        "provider": provider,
        "mode": mode,
        "state": state,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "issued_at": timezone.now().timestamp(),
        "invitation_id": str(invitation_id) if invitation_id else None,
        "bound_user_id": (
            str(request.user.id)
            if mode == "invitation" and request.user.is_authenticated
            else None
        ),
    }
    request.session[FLOW_SESSION_KEY] = flow
    request.session.modified = True

    return redirect(
        build_authorization_url(
            provider=provider,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=verifier,
        )
    )


def external_login(request, *, provider: str):
    try:
        return _start_flow(request, provider=provider, mode="login")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
        return redirect("login")


@login_required
@require_POST
def external_link(request, *, provider: str):
    try:
        return _start_flow(request, provider=provider, mode="link")
    except ValidationError as exc:
        messages.error(request, validation_message(exc))
        return redirect("external_auth:identities")


@never_cache
def invitation_landing(request, *, token: str):
    try:
        invitation = resolve_invitation_token(token, now=timezone.now())
    except ValidationError as exc:
        request.session.pop(INVITATION_SESSION_KEY, None)
        request.session.modified = True
        return render(
            request,
            "accounts/invitation_invalid.html",
            {"message": validation_message(exc)},
            status=410,
        )

    request.session[INVITATION_SESSION_KEY] = str(invitation.id)
    request.session.modified = True
    return render(
        request,
        "accounts/invitation_landing.html",
        {
            "invitation": invitation,
            "providers": configured_providers(),
        },
    )


def invitation_external_login(request, *, provider: str):
    raw_invitation_id = request.session.get(INVITATION_SESSION_KEY)
    if not raw_invitation_id:
        messages.error(request, "Сначала откройте действующую ссылку-приглашение.")
        return redirect("login")
    try:
        invitation_id = UUID(raw_invitation_id)
        invitation = AccountInvitation.objects.get(pk=invitation_id)
        now = timezone.now()
        if (
            invitation.accepted_at is not None
            or invitation.revoked_at is not None
            or invitation.expires_at <= now
        ):
            raise ValidationError({"invitation": "Invitation is no longer active."})
        return _start_flow(
            request,
            provider=provider,
            mode="invitation",
            invitation_id=invitation.id,
        )
    except (AccountInvitation.DoesNotExist, ValueError, ValidationError) as exc:
        request.session.pop(INVITATION_SESSION_KEY, None)
        request.session.modified = True
        message = (
            validation_message(exc)
            if isinstance(exc, ValidationError)
            else "Приглашение больше недействительно."
        )
        messages.error(request, message)
        return redirect("login")


@never_cache
def external_callback(request, *, provider: str):
    flow = request.session.pop(FLOW_SESSION_KEY, None)
    request.session.modified = True
    if not isinstance(flow, dict):
        messages.error(request, "Сессия внешнего входа отсутствует или уже использована.")
        return redirect("login")

    expected_state = str(flow.get("state") or "")
    returned_state = str(request.GET.get("state") or "")
    if (
        flow.get("provider") != provider
        or not expected_state
        or not returned_state
        or not secrets.compare_digest(expected_state, returned_state)
    ):
        messages.error(request, "Проверка state внешнего входа не пройдена.")
        return redirect("login")

    issued_at = flow.get("issued_at")
    ttl = settings.EXTERNAL_AUTH_FLOW_TTL_SECONDS
    try:
        age = timezone.now().timestamp() - float(issued_at)
    except (TypeError, ValueError):
        age = ttl + 1
    if age < 0 or age > ttl:
        messages.error(request, "Сессия внешнего входа истекла.")
        return redirect("login")

    provider_error = request.GET.get("error")
    if provider_error:
        description = request.GET.get("error_description") or provider_error
        messages.error(request, f"Внешний вход отменён: {description}")
        return redirect("login")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "Провайдер не вернул код авторизации.")
        return redirect("login")

    try:
        profile = exchange_authorization_code(
            provider=provider,
            code=code,
            redirect_uri=str(flow["redirect_uri"]),
            state=expected_state,
            code_verifier=str(flow["code_verifier"]),
            device_id=request.GET.get("device_id"),
        )
        mode = flow.get("mode")
        if mode == "link":
            if not request.user.is_authenticated:
                raise ValidationError({"user": "Sign in before linking a provider."})
            link_external_identity(
                user=request.user,
                provider=profile.provider,
                provider_subject=profile.subject,
                now=timezone.now(),
            )
            messages.success(request, "Внешний аккаунт подключён.")
            return redirect("external_auth:identities")

        invitation_id = None
        if mode == "invitation":
            raw_id = flow.get("invitation_id")
            if not raw_id:
                raise ValidationError({"invitation": "Invitation is missing."})
            invitation_id = UUID(str(raw_id))

            bound_user_id = flow.get("bound_user_id")
            if bound_user_id is not None:
                if (
                    not request.user.is_authenticated
                    or str(request.user.id) != str(bound_user_id)
                ):
                    raise ValidationError(
                        {
                            "user": (
                                "The signed-in account changed during "
                                "invitation acceptance."
                            )
                        }
                    )
                user = accept_account_invitation_for_existing_user(
                    user=request.user,
                    provider=profile.provider,
                    provider_subject=profile.subject,
                    invitation_id=invitation_id,
                    now=timezone.now(),
                )
            else:
                user = authenticate_external_identity(
                    provider=profile.provider,
                    provider_subject=profile.subject,
                    invitation_id=invitation_id,
                    now=timezone.now(),
                )
        else:
            user = authenticate_external_identity(
                provider=profile.provider,
                provider_subject=profile.subject,
                invitation_id=None,
                now=timezone.now(),
            )
        django_login(
            request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        request.session.pop(INVITATION_SESSION_KEY, None)
        messages.success(request, "Вход выполнен.")
        return redirect("scheduling:home")
    except (ValidationError, ValueError) as exc:
        message = (
            validation_message(exc)
            if isinstance(exc, ValidationError)
            else "Некорректные данные внешнего входа."
        )
        messages.error(request, message)
        if flow.get("mode") == "link" and request.user.is_authenticated:
            return redirect("external_auth:identities")
        return redirect("login")


@login_required
def external_identities(request):
    identities = tuple(
        request.user.external_identities.order_by("provider", "id")
    )
    linked = {identity.provider for identity in identities}
    available = tuple(
        provider
        for provider in configured_providers()
        if provider not in linked
    )
    return render(
        request,
        "accounts/external_identities.html",
        {
            "identities": identities,
            "available_providers": available,
        },
    )
