from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.models import (
    AccountInvitation,
    CoachProfile,
    ExternalIdentity,
    Student,
    StudentAccess,
)
from audit.models import AuditEvent

from accounts.onboarding import (
    accept_account_invitation_for_existing_user,
    authenticate_external_identity,
    create_account_invitation,
    link_external_identity,
    resolve_invitation_token,
    revoke_account_invitation,
)

User = get_user_model()


@pytest.fixture
def manager(db):
    return User.objects.create_superuser(
        username="identity-manager",
        password="test",
    )


@pytest.mark.django_db
def test_student_invitation_provisions_external_only_user(manager):
    student = Student.objects.create(display_name="Маша")
    created = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.GUARDIAN,
        account_display_name="Родитель ученика",
    )

    assert created.token not in created.invitation.token_hash
    assert resolve_invitation_token(created.token).id == created.invitation.id

    user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="pairwise-yandex-subject",
        invitation_id=created.invitation.id,
    )

    assert user.username.startswith("u_")
    assert user.has_usable_password() is False
    identity = ExternalIdentity.objects.get(user=user)
    assert identity.provider == ExternalIdentity.Provider.YANDEX
    assert identity.provider_subject == "pairwise-yandex-subject"
    access = StudentAccess.objects.get(user=user, student=student)
    assert access.role == StudentAccess.Role.GUARDIAN
    assert access.is_active is True

    created.invitation.refresh_from_db()
    assert created.invitation.accepted_by_id == user.id
    assert created.invitation.accepted_at is not None


@pytest.mark.django_db
def test_unknown_external_identity_requires_invitation():
    with pytest.raises(ValidationError) as exc:
        authenticate_external_identity(
            provider=ExternalIdentity.Provider.VK,
            provider_subject="vk-user",
        )

    assert "external_identity" in exc.value.message_dict
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_invitation_is_one_time_even_for_existing_identity(manager):
    student = Student.objects.create(display_name="Ученик")
    first = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.SELF,
        account_display_name="Ученик",
    )
    user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="same-subject",
        invitation_id=first.invitation.id,
    )

    with pytest.raises(ValidationError):
        authenticate_external_identity(
            provider=ExternalIdentity.Provider.YANDEX,
            provider_subject="same-subject",
            invitation_id=first.invitation.id,
        )

    assert ExternalIdentity.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_existing_identity_can_accept_another_student_invitation(manager):
    first_student = Student.objects.create(display_name="Первый")
    second_student = Student.objects.create(display_name="Второй")
    first = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=first_student.id,
        student_access_role=StudentAccess.Role.GUARDIAN,
        account_display_name="Родитель ученика",
    )
    user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.VK,
        provider_subject="vk-parent",
        invitation_id=first.invitation.id,
    )
    second = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=second_student.id,
        student_access_role=StudentAccess.Role.GUARDIAN,
        account_display_name="Родитель ученика",
    )

    same_user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.VK,
        provider_subject="vk-parent",
        invitation_id=second.invitation.id,
    )

    assert same_user.id == user.id
    assert StudentAccess.objects.filter(user=user, is_active=True).count() == 2


@pytest.mark.django_db
def test_coach_invitation_creates_coach_profile(manager):
    created = create_account_invitation(
        kind=AccountInvitation.Kind.COACH,
        actor=manager,
        coach_display_name="Анна Тренер",
    )

    user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.VK,
        provider_subject="coach-vk-id",
        invitation_id=created.invitation.id,
    )

    coach = CoachProfile.objects.get(user=user)
    assert coach.display_name == "Анна Тренер"
    assert coach.is_active is True


@pytest.mark.django_db
def test_link_second_provider_but_not_second_subject_for_same_provider(manager):
    user = User.objects.create_user(username="linked-user")
    first = link_external_identity(
        user=user,
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="yandex-1",
    )
    same = link_external_identity(
        user=user,
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="yandex-1",
    )
    assert same.id == first.id

    with pytest.raises(ValidationError):
        link_external_identity(
            user=user,
            provider=ExternalIdentity.Provider.YANDEX,
            provider_subject="yandex-2",
        )

    vk = link_external_identity(
        user=user,
        provider=ExternalIdentity.Provider.VK,
        provider_subject="vk-1",
    )
    assert vk.user_id == user.id
    assert user.external_identities.count() == 2


@pytest.mark.django_db
def test_external_subject_cannot_be_linked_to_two_users():
    first = User.objects.create_user(username="first")
    second = User.objects.create_user(username="second")
    link_external_identity(
        user=first,
        provider=ExternalIdentity.Provider.VK,
        provider_subject="shared-vk-subject",
    )

    with pytest.raises(ValidationError):
        link_external_identity(
            user=second,
            provider=ExternalIdentity.Provider.VK,
            provider_subject="shared-vk-subject",
        )


@pytest.mark.django_db
def test_revoked_and_expired_invitation_cannot_be_used(manager):
    student = Student.objects.create(display_name="Ученик")
    revoked = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.SELF,
        account_display_name="Ученик",
    )
    revoke_account_invitation(
        invitation_id=revoked.invitation.id,
        actor=manager,
    )
    with pytest.raises(ValidationError):
        resolve_invitation_token(revoked.token)

    expired = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.SELF,
        account_display_name="Ученик",
        expires_at=timezone.now() + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError):
        resolve_invitation_token(
            expired.token,
            now=timezone.now() + timedelta(seconds=2),
        )



@pytest.mark.django_db
def test_invitation_permission_does_not_bypass_target_permission():
    actor = User.objects.create_user(username="limited-inviter")
    actor.user_permissions.add(
        Permission.objects.get(
            codename="add_accountinvitation",
            content_type__app_label="accounts",
        )
    )
    student = Student.objects.create(display_name="Ученик")

    with pytest.raises(PermissionDenied):
        create_account_invitation(
            kind=AccountInvitation.Kind.STUDENT_ACCESS,
            actor=actor,
            student_id=student.id,
            student_access_role=StudentAccess.Role.GUARDIAN,
        )



@pytest.mark.django_db
def test_invitation_provisioning_emits_role_audit_events(manager):
    student = Student.objects.create(display_name="Аудит")
    student_invite = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.SELF,
        account_display_name="Ученик",
    )
    student_user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="audit-student",
        invitation_id=student_invite.invitation.id,
    )
    access = StudentAccess.objects.get(user=student_user, student=student)
    assert AuditEvent.objects.filter(
        event_type="StudentAccessCreated",
        aggregate_id=access.id,
    ).exists()

    coach_invite = create_account_invitation(
        kind=AccountInvitation.Kind.COACH,
        actor=manager,
        coach_display_name="Аудит Тренер",
    )
    coach_user = authenticate_external_identity(
        provider=ExternalIdentity.Provider.VK,
        provider_subject="audit-coach",
        invitation_id=coach_invite.invitation.id,
    )
    coach = CoachProfile.objects.get(user=coach_user)
    assert AuditEvent.objects.filter(
        event_type="CoachProfileCreated",
        aggregate_id=coach.id,
    ).exists()



@pytest.mark.django_db
def test_existing_user_invitation_acceptance_rolls_back_provider_link_on_failure(
    manager,
):
    user = User.objects.create_user(username="rollback-user", password="test")
    student = Student.objects.create(display_name="Rollback student")
    created = create_account_invitation(
        kind=AccountInvitation.Kind.STUDENT_ACCESS,
        actor=manager,
        student_id=student.id,
        student_access_role=StudentAccess.Role.GUARDIAN,
        account_display_name="Родитель ученика",
    )
    revoke_account_invitation(
        invitation_id=created.invitation.id,
        actor=manager,
    )

    with pytest.raises(ValidationError):
        accept_account_invitation_for_existing_user(
            user=user,
            provider=ExternalIdentity.Provider.VK,
            provider_subject="rollback-vk",
            invitation_id=created.invitation.id,
        )

    assert ExternalIdentity.objects.filter(
        user=user,
        provider=ExternalIdentity.Provider.VK,
    ).exists() is False
    assert StudentAccess.objects.filter(
        user=user,
        student=student,
    ).exists() is False
