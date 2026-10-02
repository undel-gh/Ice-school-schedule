from django.urls import path

from . import mfa_views


app_name = "mfa"

urlpatterns = [
    path("challenge/", mfa_views.mfa_challenge, name="challenge"),
    path("setup/", mfa_views.mfa_setup, name="setup"),
    path("setup/qr/", mfa_views.mfa_setup_qr, name="setup_qr"),
    path(
        "recovery-codes/",
        mfa_views.mfa_recovery_codes,
        name="recovery_codes",
    ),
    path("security/", mfa_views.mfa_security, name="security"),
    path(
        "security/recovery-codes/regenerate/",
        mfa_views.mfa_regenerate_recovery_codes,
        name="regenerate_recovery_codes",
    ),
    path(
        "security/authenticator/replace/",
        mfa_views.mfa_replace_authenticator,
        name="replace_authenticator",
    ),
]
