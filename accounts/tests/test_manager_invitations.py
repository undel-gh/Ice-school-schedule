from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import AccountInvitation, ExternalIdentity, Student, StudentAccess
from audit.models import AuditEvent

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_superuser(
        username="manager-invitations",
        password="test",
    )


@pytest.mark.django_db
def test_manager_creates_student_invitation_link(client, manager):
    student = Student.objects.create(display_name="Маша")
    client.force_login(manager)

    response = client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.STUDENT_ACCESS,
            "student": str(student.id),
            "student_access_role": "guardian",
            "account_display_name": "Мама Маши",
            "coach_display_name": "",
            "expires_in_hours": "24",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "/accounts/external/invite/" in body
    invitation = AccountInvitation.objects.get()
    assert invitation.student_id == student.id
    assert invitation.student_access_role == "guardian"
    assert invitation.account_display_name == "Мама Маши"
    assert len(invitation.token_hash) == 64
    assert invitation.token_hash not in body
    assert AuditEvent.objects.filter(
        event_type="AccountInvitationCreated",
        aggregate_id=invitation.id,
    ).exists()


@pytest.mark.django_db
def test_manager_creates_coach_invitation(client, manager):
    client.force_login(manager)

    response = client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.COACH,
            "student": "",
            "student_access_role": "",
            "account_display_name": "",
            "coach_display_name": "Анна Тренер",
            "expires_in_hours": "168",
        },
    )

    assert response.status_code == 200
    invitation = AccountInvitation.objects.get()
    assert invitation.kind == AccountInvitation.Kind.COACH
    assert invitation.coach_display_name == "Анна Тренер"
    assert invitation.student_id is None


@pytest.mark.django_db
def test_manager_can_revoke_unused_invitation(client, manager):
    client.force_login(manager)
    client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.COACH,
            "student": "",
            "student_access_role": "",
            "account_display_name": "",
            "coach_display_name": "Тренер",
            "expires_in_hours": "24",
        },
    )
    invitation = AccountInvitation.objects.get()

    response = client.post(
        reverse(
            "accounts_manager:invitation_revoke",
            kwargs={"invitation_id": invitation.id},
        )
    )

    assert response.status_code == 302
    invitation.refresh_from_db()
    assert invitation.revoked_at is not None
    assert invitation.revoked_by_id == manager.id


@pytest.mark.django_db
def test_manager_invitation_form_rejects_incomplete_target(client, manager):
    client.force_login(manager)

    response = client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.STUDENT_ACCESS,
            "student": "",
            "student_access_role": "",
            "account_display_name": "",
            "coach_display_name": "",
            "expires_in_hours": "24",
        },
    )

    assert response.status_code == 200
    assert AccountInvitation.objects.count() == 0
    body = response.content.decode()
    assert "Выберите ученика" in body



@pytest.mark.django_db
def test_manager_invitation_revoke_rejects_get(client, manager):
    client.force_login(manager)
    student = Student.objects.create(display_name="GET revoke")
    response = client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.STUDENT_ACCESS,
            "student": str(student.id),
            "student_access_role": "self",
            "account_display_name": "Ученик",
            "coach_display_name": "",
            "expires_in_hours": "24",
        },
    )
    assert response.status_code == 200
    invitation = AccountInvitation.objects.get()

    get_response = client.get(
        reverse(
            "accounts_manager:invitation_revoke",
            kwargs={"invitation_id": invitation.id},
        )
    )

    assert get_response.status_code == 405
    invitation.refresh_from_db()
    assert invitation.revoked_at is None



@pytest.mark.django_db
def test_manager_unlinks_compromised_identity_from_student_account(
    client,
    manager,
):
    user = User.objects.create_user(
        username="parent-with-two-providers",
        display_name="Мама Ани",
    )
    student = Student.objects.create(display_name="Аня")
    StudentAccess.objects.create(
        user=user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
        is_active=True,
    )
    safe = ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="safe-yandex-manager-view",
    )
    compromised = ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentity.Provider.VK,
        provider_subject="stolen-vk-manager-view",
    )
    client.force_login(manager)

    detail = client.get(
        reverse(
            "accounts_manager:student_detail",
            kwargs={"student_id": student.id},
        )
    )
    assert detail.status_code == 200
    body = detail.content.decode()
    assert "Мама Ани" in body
    assert "stolen-vk-manager-view" not in body
    assert "Отвязать VK" in body

    response = client.post(
        reverse(
            "accounts_manager:external_identity_unlink",
            kwargs={"identity_id": compromised.id},
        )
    )

    assert response.status_code == 302
    assert response.url == reverse(
        "accounts_manager:student_detail",
        kwargs={"student_id": student.id},
    )
    assert ExternalIdentity.objects.filter(pk=compromised.id).exists() is False
    assert ExternalIdentity.objects.filter(pk=safe.id).exists()
    assert AuditEvent.objects.filter(
        event_type="ExternalIdentityUnlinked",
        aggregate_id=compromised.id,
    ).exists()



@pytest.mark.django_db
def test_manager_web_can_deactivate_unlink_and_issue_recovery_invitation(
    client,
    manager,
):
    user = User.objects.create_user(
        username="recovery-web-parent",
        display_name="Мама Лизы",
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    student = Student.objects.create(display_name="Лиза")
    StudentAccess.objects.create(
        user=user,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
        is_active=True,
    )
    identity = ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentity.Provider.VK,
        provider_subject="recovery-web-vk",
    )
    client.force_login(manager)

    deactivate = client.post(
        reverse(
            "accounts_manager:external_user_deactivate_for_recovery",
            kwargs={"user_id": user.id},
        )
    )
    assert deactivate.status_code == 302
    user.refresh_from_db()
    assert user.is_active is False

    unlink = client.post(
        reverse(
            "accounts_manager:external_identity_unlink",
            kwargs={"identity_id": identity.id},
        )
    )
    assert unlink.status_code == 302
    assert ExternalIdentity.objects.filter(pk=identity.id).exists() is False

    form_page = client.get(
        reverse("accounts_manager:invitation_create"),
        {"recovery_user": str(user.id)},
    )
    assert form_page.status_code == 200
    form_body = form_page.content.decode()
    assert "Восстановление доступа" in form_body
    assert "независимому доверенному" in form_body

    created = client.post(
        reverse("accounts_manager:invitation_create"),
        {
            "kind": AccountInvitation.Kind.RECOVERY,
            "student": "",
            "student_access_role": "",
            "recovery_user": str(user.id),
            "account_display_name": "",
            "coach_display_name": "",
            "expires_in_hours": "24",
        },
    )

    assert created.status_code == 200
    invitation = AccountInvitation.objects.get(kind=AccountInvitation.Kind.RECOVERY)
    assert invitation.recovery_user_id == user.id
    assert invitation.account_display_name == "Мама Лизы"
    created_body = created.content.decode()
    assert "/accounts/external/invite/" in created_body
    assert "независимому доверенному каналу" in created_body
    assert "скомпрометированным аккаунтом" in created_body
