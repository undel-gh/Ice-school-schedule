from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.external_auth import ExternalProfile
from accounts.models import AccountInvitation, ExternalIdentity, Student, StudentAccess
from accounts.onboarding import create_account_invitation

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_superuser(
        username="invite-manager",
        password="test",
    )


@pytest.mark.django_db
def test_invitation_callback_creates_user_access_and_session(
    client,
    manager,
    settings,
    monkeypatch,
):
    settings.YANDEX_OAUTH_CLIENT_ID = "ya-client"
    student = Student.objects.create(display_name="Маша")
    created = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.GUARDIAN,
    )

    landing = client.get(
        reverse(
            "external_auth:invitation",
            kwargs={"token": created.token},
        )
    )
    assert landing.status_code == 200

    begin = client.get(
        reverse(
            "external_auth:invitation_login",
            kwargs={"provider": "yandex"},
        )
    )
    assert begin.status_code == 302
    flow = client.session["external_auth_flow"]

    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        lambda **kwargs: ExternalProfile(
            provider=ExternalIdentity.Provider.YANDEX,
            subject="ya-subject",
        ),
    )
    callback = client.get(
        reverse(
            "external_auth:callback",
            kwargs={"provider": "yandex"},
        ),
        {"code": "code", "state": flow["state"]},
    )

    assert callback.status_code == 302
    assert callback.url == reverse("scheduling:home")
    identity = ExternalIdentity.objects.get(provider_subject="ya-subject")
    assert str(client.session["_auth_user_id"]) == str(identity.user_id)
    assert StudentAccess.objects.filter(
        user=identity.user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
        is_active=True,
    ).exists()


@pytest.mark.django_db
def test_plain_login_does_not_auto_provision_unknown_identity(
    client,
    settings,
    monkeypatch,
):
    settings.VKID_CLIENT_ID = "12345"

    begin = client.get(
        reverse("external_auth:login", kwargs={"provider": "vk"})
    )
    flow = client.session["external_auth_flow"]
    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        lambda **kwargs: ExternalProfile(
            provider=ExternalIdentity.Provider.VK,
            subject="unknown-vk",
        ),
    )

    callback = client.get(
        reverse("external_auth:callback", kwargs={"provider": "vk"}),
        {
            "code": "code",
            "state": flow["state"],
            "device_id": "device",
        },
    )

    assert callback.status_code == 302
    assert callback.url == reverse("login")
    assert ExternalIdentity.objects.filter(
        provider_subject="unknown-vk"
    ).exists() is False


@pytest.mark.django_db
def test_existing_external_identity_can_log_in(
    client,
    settings,
    monkeypatch,
):
    settings.YANDEX_OAUTH_CLIENT_ID = "ya-client"
    user = User.objects.create_user(username="existing")
    ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="existing-ya",
    )

    client.get(
        reverse("external_auth:login", kwargs={"provider": "yandex"})
    )
    flow = client.session["external_auth_flow"]
    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        lambda **kwargs: ExternalProfile(
            provider=ExternalIdentity.Provider.YANDEX,
            subject="existing-ya",
        ),
    )

    callback = client.get(
        reverse(
            "external_auth:callback",
            kwargs={"provider": "yandex"},
        ),
        {"code": "code", "state": flow["state"]},
    )

    assert callback.status_code == 302
    assert str(client.session["_auth_user_id"]) == str(user.id)


@pytest.mark.django_db
def test_state_mismatch_rejects_callback_without_exchange(
    client,
    settings,
    monkeypatch,
):
    settings.YANDEX_OAUTH_CLIENT_ID = "ya-client"
    client.get(
        reverse("external_auth:login", kwargs={"provider": "yandex"})
    )
    called = False

    def fake_exchange(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not exchange mismatched state")

    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        fake_exchange,
    )
    callback = client.get(
        reverse(
            "external_auth:callback",
            kwargs={"provider": "yandex"},
        ),
        {"code": "code", "state": "wrong"},
    )

    assert callback.status_code == 302
    assert called is False
    assert "external_auth_flow" not in client.session


@pytest.mark.django_db
def test_authenticated_user_can_link_second_provider(
    client,
    settings,
    monkeypatch,
):
    settings.VKID_CLIENT_ID = "12345"
    user = User.objects.create_user(username="local-user", password="test")
    client.force_login(user)

    begin = client.get(
        reverse("external_auth:link", kwargs={"provider": "vk"})
    )
    assert begin.status_code == 302
    flow = client.session["external_auth_flow"]
    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        lambda **kwargs: ExternalProfile(
            provider=ExternalIdentity.Provider.VK,
            subject="linked-vk",
        ),
    )

    callback = client.get(
        reverse("external_auth:callback", kwargs={"provider": "vk"}),
        {
            "code": "code",
            "state": flow["state"],
            "device_id": "device",
        },
    )

    assert callback.status_code == 302
    assert callback.url == reverse("external_auth:identities")
    assert ExternalIdentity.objects.filter(
        user=user,
        provider=ExternalIdentity.Provider.VK,
        provider_subject="linked-vk",
    ).exists()
