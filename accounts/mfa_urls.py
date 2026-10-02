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
]
