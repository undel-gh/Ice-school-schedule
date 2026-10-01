from django.urls import path

from . import external_views

app_name = "external_auth"

urlpatterns = [
    path(
        "login/<str:provider>/",
        external_views.external_login,
        name="login",
    ),
    path(
        "link/<str:provider>/",
        external_views.external_link,
        name="link",
    ),
    path(
        "callback/<str:provider>/",
        external_views.external_callback,
        name="callback",
    ),
    path(
        "invite/<str:token>/",
        external_views.invitation_landing,
        name="invitation",
    ),
    path(
        "invite-login/<str:provider>/",
        external_views.invitation_external_login,
        name="invitation_login",
    ),
    path(
        "identities/",
        external_views.external_identities,
        name="identities",
    ),
    path(
        "identities/<uuid:identity_id>/unlink/",
        external_views.external_identity_unlink,
        name="unlink",
    ),
]
