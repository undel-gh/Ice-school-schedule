from __future__ import annotations

from base64 import b32encode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp import login as otp_login
from django_otp import verify_token
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.qr import write_qrcode_image

from audit.services import record_event

from .mfa import (
    MFA_RECOVERY_CODES_SESSION_KEY,
    MFA_SETUP_DEVICE_SESSION_KEY,
    MFA_VERIFIED_AT_SESSION_KEY,
    MFA_LOGIN_METHOD_SESSION_KEY,
    authenticated_mfa_setup_is_authorized,
    authorize_authenticated_mfa_setup,
    begin_mfa_preauth,
    begin_totp_replacement,
    clear_mfa_transient_session,
    has_confirmed_mfa_device,
    has_confirmed_totp,
    local_password_login_requires_mfa,
    mark_privileged_mfa_verified,
    mfa_required_for_user,
    pop_mfa_next,
    privileged_mfa_verification_is_fresh,
    remember_mfa_next,
    resolve_mfa_identity,
    totp_replacement_old_device_ids,
)
from .mfa_forms import (
    LocalizedOTPTokenForm,
    MFAPasswordReauthForm,
    MFAOptionalDisableForm,
    MFASetupTokenForm,
)


RECOVERY_CODE_COUNT = 10


def _create_recovery_codes(user) -> list[str]:
    StaticDevice.objects.filter(user=user).delete()
    recovery_device = StaticDevice.objects.create(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    codes = [
        StaticToken.random_token()
        for _ in range(RECOVERY_CODE_COUNT)
    ]
    StaticToken.objects.bulk_create(
        [
            StaticToken(device=recovery_device, token=code)
            for code in codes
        ]
    )
    return codes


def _security_user(request):
    user = request.user
    if not user.is_authenticated or not user.has_usable_password():
        return None
    mandatory = mfa_required_for_user(user)
    if mandatory or has_confirmed_totp(user):
        if (
            not getattr(user, "is_verified", lambda: False)()
            or not privileged_mfa_verification_is_fresh(request)
        ):
            return None
        # OAuth may access the ordinary UI without TOTP, but security
        # mutations require a locally OTP-verified session.
        if (
            not mandatory
            and request.session.get(MFA_LOGIN_METHOD_SESSION_KEY) == "external"
        ):
            return None
    return user


def _security_context(user, **form_overrides):
    mandatory = mfa_required_for_user(user)
    enabled = mandatory or has_confirmed_totp(user)
    result = {
        "mfa_optional": not mandatory,
        "mfa_enabled": enabled,
        "recovery_code_count": (
            StaticToken.objects.filter(
                device__user=user,
                device__confirmed=True,
            ).count()
            if enabled else 0
        ),
        "enable_form": MFAPasswordReauthForm(user),
        "regenerate_form": MFAPasswordReauthForm(user),
        "replace_form": MFAPasswordReauthForm(user),
        "disable_form": (
            MFAOptionalDisableForm(user)
            if not mandatory and enabled
            else None
        ),
    }
    result.update(form_overrides)
    return result


def _security_denied(request):
    if request.user.is_authenticated and not request.user.has_usable_password():
        return redirect("external_auth:identities")
    query = urlencode({"next": reverse("mfa:security")})
    return redirect(f"{reverse('login')}?{query}")


class LocalLoginView(LoginView):
    template_name = "registration/login.html"

    def form_valid(self, form):
        user = form.get_user()
        if not local_password_login_requires_mfa(user):
            response = super().form_valid(form)
            self.request.session[MFA_LOGIN_METHOD_SESSION_KEY] = "local"
            return response

        if self.request.user.is_authenticated:
            # Never complete second factor inside an earlier OAuth session.
            django_logout(self.request)
        begin_mfa_preauth(
            self.request,
            user=user,
            backend=getattr(
                user,
                "backend",
                "django.contrib.auth.backends.ModelBackend",
            ),
            next_url=self.get_success_url(),
        )
        if has_confirmed_mfa_device(user):
            return redirect("mfa:challenge")
        return redirect("mfa:setup")


def _response_no_store(response):
    response["Cache-Control"] = "no-store, no-cache, max-age=0"
    response["Pragma"] = "no-cache"
    return response


def _mfa_identity_or_login(request):
    identity = resolve_mfa_identity(request)
    if identity is None:
        messages.error(
            request,
            "Сеанс проверки входа отсутствует или истёк. Войдите снова.",
        )
        return None, redirect("login")
    if (
        request.user.is_authenticated
        and not mfa_required_for_user(identity.user)
    ):
        return identity, None
    if not identity.user.has_usable_password():
        clear_mfa_transient_session(request)
        if request.user.is_authenticated:
            django_logout(request)
        messages.error(
            request,
            "Привилегированный аккаунт требует локальный пароль как первый "
            "фактор. Обратитесь к администратору.",
        )
        return None, redirect("login")
    return identity, None


def _finish_verified_login(request, *, identity, device) -> None:
    user = identity.user
    user.otp_device = device
    if request.user.is_authenticated:
        if request.user.pk != user.pk:
            raise RuntimeError("MFA identity changed during verification.")
        request.session.cycle_key()
        otp_login(request, device)
    else:
        django_login(
            request,
            user,
            backend=identity.backend,
        )
        otp_login(request, device)
    mark_privileged_mfa_verified(request)
    request.session[MFA_LOGIN_METHOD_SESSION_KEY] = "local"
    request.session.set_expiry(
        int(
            getattr(
                settings,
                "MFA_PRIVILEGED_SESSION_MAX_AGE_SECONDS",
                43200,
            )
        )
    )


@never_cache
def mfa_challenge(request):
    identity, failure = _mfa_identity_or_login(request)
    if failure is not None:
        return failure
    user = identity.user
    if not mfa_required_for_user(user):
        if not has_confirmed_totp(user):
            return redirect("scheduling:home")
        if (
            request.user.is_authenticated
            and request.session.get(MFA_LOGIN_METHOD_SESSION_KEY) == "external"
        ):
            return redirect("scheduling:home")
    if request.user.is_authenticated:
        is_verified = getattr(
            request.user,
            "is_verified",
            lambda: False,
        )()
        if (
            not is_verified
            or not privileged_mfa_verification_is_fresh(request)
        ):
            target = request.session.get(
                "mfa_next",
                reverse("scheduling:home"),
            )
            django_logout(request)
            query = urlencode({"next": target})
            return redirect(f"{reverse('login')}?{query}")
    if not has_confirmed_mfa_device(user):
        return redirect("mfa:setup")

    form = LocalizedOTPTokenForm(
        user,
        request=request,
        data=request.POST or None,
    )
    if request.method == "POST" and form.is_valid():
        device = getattr(user, "otp_device", None)
        if device is None:
            form.add_error("otp_token", "Код не подтверждён.")
        else:
            _finish_verified_login(
                request,
                identity=identity,
                device=device,
            )
            clear_mfa_transient_session(request)
            record_event(
                event_type="MFAAuthenticated",
                aggregate_type="User",
                aggregate_id=user.id,
                actor=user,
                payload={
                    "device_type": device.__class__.__name__,
                },
            )
            if isinstance(device, StaticDevice):
                record_event(
                    event_type="MFARecoveryCodeUsed",
                    aggregate_type="User",
                    aggregate_id=user.id,
                    actor=user,
                    payload={},
                )
            messages.success(request, "Дополнительная проверка пройдена.")
            if not has_confirmed_totp(user):
                if identity.preauthenticated:
                    authorize_authenticated_mfa_setup(
                        request,
                        user=user,
                    )
                return redirect("mfa:setup")
            target = pop_mfa_next(
                request,
                fallback=reverse("scheduling:home"),
            )
            return redirect(target)

    return _response_no_store(
        render(
            request,
            "accounts/mfa_challenge.html",
            {"form": form},
        )
    )


def _setup_device_for(user, request) -> TOTPDevice:
    raw_id = request.session.get(MFA_SETUP_DEVICE_SESSION_KEY)
    if raw_id is not None:
        device = TOTPDevice.objects.filter(
            pk=raw_id,
            user=user,
            confirmed=False,
        ).first()
        if device is not None:
            return device

    device = (
        TOTPDevice.objects.filter(
            user=user,
            confirmed=False,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if device is None:
        device = TOTPDevice.objects.create(
            user=user,
            name="Authenticator",
            confirmed=False,
        )
    TOTPDevice.objects.filter(
        user=user,
        confirmed=False,
    ).exclude(pk=device.pk).delete()
    request.session[MFA_SETUP_DEVICE_SESSION_KEY] = device.pk
    request.session.modified = True
    return device


@never_cache
def mfa_setup(request):
    identity, failure = _mfa_identity_or_login(request)
    if failure is not None:
        return failure
    user = identity.user
    if not mfa_required_for_user(user):
        if not user.has_usable_password():
            clear_mfa_transient_session(request)
            return redirect("scheduling:home")
        if (
            not identity.preauthenticated
            and not authenticated_mfa_setup_is_authorized(
                request, user=user
            )
        ):
            return redirect("mfa:security")
    replacement_old_device_ids = totp_replacement_old_device_ids(
        request,
        user=user,
    )
    if (
        has_confirmed_totp(user)
        and replacement_old_device_ids is None
    ):
        return redirect(
            "mfa:challenge" if mfa_required_for_user(user) else "mfa:security"
        )
    if (
        request.user.is_authenticated
        and not identity.preauthenticated
        and not authenticated_mfa_setup_is_authorized(
            request,
            user=user,
        )
    ):
        target = request.session.get(
            "mfa_next",
            reverse("scheduling:home"),
        )
        django_logout(request)
        query = urlencode({"next": target})
        return redirect(f"{reverse('login')}?{query}")

    device = _setup_device_for(user, request)
    form = MFASetupTokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        verified = verify_token(
            user,
            device.persistent_id,
            form.cleaned_data["token"],
        )
        if verified is None:
            form.add_error("token", "Неверный код. Проверьте время на устройстве.")
        else:
            with transaction.atomic():
                locked = TOTPDevice.objects.select_for_update().get(
                    pk=device.pk,
                    user=user,
                    confirmed=False,
                )
                locked.confirmed = True
                locked.save(update_fields=["confirmed"])
                if replacement_old_device_ids is not None:
                    TOTPDevice.objects.filter(
                        user=user,
                        pk__in=replacement_old_device_ids,
                        confirmed=True,
                    ).exclude(pk=locked.pk).delete()
                TOTPDevice.objects.filter(
                    user=user,
                    confirmed=False,
                ).exclude(pk=locked.pk).delete()

                codes = _create_recovery_codes(user)

            _finish_verified_login(
                request,
                identity=identity,
                device=locked,
            )
            clear_mfa_transient_session(request)
            request.session[MFA_RECOVERY_CODES_SESSION_KEY] = codes
            request.session.modified = True
            record_event(
                event_type=(
                    "MFAAuthenticatorReplaced"
                    if replacement_old_device_ids is not None
                    else "MFAEnrolled"
                ),
                aggregate_type="User",
                aggregate_id=user.id,
                actor=user,
                payload={
                    "method": "totp",
                    "recovery_code_count": len(codes),
                },
            )
            return redirect("mfa:recovery_codes")

    manual_secret = b32encode(device.bin_key).decode("ascii").rstrip("=")
    return _response_no_store(
        render(
            request,
            "accounts/mfa_setup.html",
            {
                "form": form,
                "manual_secret": manual_secret,
            },
        )
    )


@never_cache
def mfa_setup_qr(request):
    identity, failure = _mfa_identity_or_login(request)
    if failure is not None:
        return failure
    user = identity.user
    if (
        request.user.is_authenticated
        and not identity.preauthenticated
        and not authenticated_mfa_setup_is_authorized(
            request,
            user=user,
        )
    ):
        return HttpResponse(status=404)
    raw_id = request.session.get(MFA_SETUP_DEVICE_SESSION_KEY)
    device = TOTPDevice.objects.filter(
        pk=raw_id,
        user=user,
        confirmed=False,
    ).first()
    if device is None:
        return HttpResponse(status=404)

    response = HttpResponse(content_type="image/svg+xml")
    write_qrcode_image(device.config_url, response)
    return _response_no_store(response)


@never_cache
def mfa_recovery_codes(request):
    if (
        not request.user.is_authenticated
        or not (
            mfa_required_for_user(request.user)
            or has_confirmed_totp(request.user)
        )
        or not getattr(request.user, "is_verified", lambda: False)()
    ):
        return redirect("mfa:challenge")

    codes = request.session.pop(MFA_RECOVERY_CODES_SESSION_KEY, None)
    request.session.modified = True
    if not codes:
        return redirect("scheduling:home")

    next_url = pop_mfa_next(
        request,
        fallback=reverse("scheduling:home"),
    )
    return _response_no_store(
        render(
            request,
            "accounts/mfa_recovery_codes.html",
            {
                "codes": codes,
                "next_url": next_url,
            },
        )
    )



@never_cache
def mfa_security(request):
    user = _security_user(request)
    if user is None:
        return _security_denied(request)
    return _response_no_store(
        render(
            request,
            "accounts/mfa_security.html",
            _security_context(user),
        )
    )


@never_cache
@require_POST
def mfa_optional_enable(request):
    user = _security_user(request)
    if user is None:
        return _security_denied(request)
    if mfa_required_for_user(user) or has_confirmed_totp(user):
        return redirect("mfa:security")

    form = MFAPasswordReauthForm(user, request.POST, request=request)
    if not form.is_valid():
        return _response_no_store(
            render(
                request,
                "accounts/mfa_security.html",
                _security_context(user, enable_form=form),
                status=400,
            )
        )

    authorize_authenticated_mfa_setup(request, user=user)
    remember_mfa_next(request, reverse("mfa:security"))
    return redirect("mfa:setup")


@never_cache
@require_POST
def mfa_optional_disable(request):
    user = _security_user(request)
    if user is None:
        return _security_denied(request)
    if mfa_required_for_user(user) or not has_confirmed_totp(user):
        return redirect("mfa:security")

    form = MFAOptionalDisableForm(user, request.POST, request=request)
    if not form.is_valid():
        return _response_no_store(
            render(
                request,
                "accounts/mfa_security.html",
                _security_context(user, disable_form=form),
                status=400,
            )
        )

    with transaction.atomic():
        locked_user = type(user).objects.select_for_update().get(pk=user.pk)
        if mfa_required_for_user(locked_user):
            return redirect("mfa:security")
        TOTPDevice.objects.filter(user=locked_user).delete()
        StaticDevice.objects.filter(user=locked_user).delete()
    request.session.pop(DEVICE_ID_SESSION_KEY, None)
    request.session.pop(MFA_VERIFIED_AT_SESSION_KEY, None)
    clear_mfa_transient_session(request)
    record_event(
        event_type="MFAOptionalDisabled",
        aggregate_type="User",
        aggregate_id=user.id,
        actor=user,
        payload={},
    )
    messages.success(request, "Двухфакторная защита входа по паролю отключена.")
    return redirect("mfa:security")


@never_cache
@require_POST
def mfa_regenerate_recovery_codes(request):
    user = _security_user(request)
    if user is None:
        return _security_denied(request)
    if not mfa_required_for_user(user) and not has_confirmed_totp(user):
        return redirect("mfa:security")

    form = MFAPasswordReauthForm(user, request.POST, request=request)
    if not form.is_valid():
        return _response_no_store(
            render(
                request,
                "accounts/mfa_security.html",
                _security_context(user, regenerate_form=form),
                status=400,
            )
        )

    with transaction.atomic():
        codes = _create_recovery_codes(user)
    request.session[MFA_RECOVERY_CODES_SESSION_KEY] = codes
    request.session.modified = True
    record_event(
        event_type="MFARecoveryCodesRegenerated",
        aggregate_type="User",
        aggregate_id=user.id,
        actor=user,
        payload={"recovery_code_count": len(codes)},
    )
    return redirect("mfa:recovery_codes")


@never_cache
@require_POST
def mfa_replace_authenticator(request):
    user = _security_user(request)
    if user is None:
        return _security_denied(request)
    if not mfa_required_for_user(user) and not has_confirmed_totp(user):
        return redirect("mfa:security")

    form = MFAPasswordReauthForm(user, request.POST, request=request)
    if not form.is_valid():
        return _response_no_store(
            render(
                request,
                "accounts/mfa_security.html",
                _security_context(user, replace_form=form),
                status=400,
            )
        )

    with transaction.atomic():
        old_device_ids = list(
            TOTPDevice.objects.select_for_update()
            .filter(user=user, confirmed=True)
            .values_list("id", flat=True)
        )
        TOTPDevice.objects.filter(
            user=user,
            confirmed=False,
        ).delete()
        pending_device = TOTPDevice.objects.create(
            user=user,
            name="Replacement authenticator",
            confirmed=False,
        )
    request.session[MFA_SETUP_DEVICE_SESSION_KEY] = pending_device.pk
    begin_totp_replacement(
        request,
        user=user,
        old_device_ids=old_device_ids,
    )
    authorize_authenticated_mfa_setup(request, user=user)
    request.session.modified = True
    record_event(
        event_type="MFAAuthenticatorReplacementStarted",
        aggregate_type="User",
        aggregate_id=user.id,
        actor=user,
        payload={},
    )
    return redirect("mfa:setup")
