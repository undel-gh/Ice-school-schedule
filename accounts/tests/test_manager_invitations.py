from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import AccountInvitation, Student
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
