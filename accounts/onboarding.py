from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import secrets
import uuid
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import record_event
from core.permissions import require_permission

from .models import (
    AccountInvitation,
    CoachProfile,
    ExternalIdentity,
    Student,
    StudentAccess,
)

User = get_user_model()


def _default_invitation_ttl() -> timedelta:
    return timedelta(
        hours=getattr(settings, "ACCOUNT_INVITATION_TTL_HOURS", 168)
    )


@dataclass(frozen=True, slots=True)
class CreatedInvitation:
    invitation: AccountInvitation
    token: str


def hash_invitation_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def resolve_invitation_token(
    token: str,
    *,
    now=None,
) -> AccountInvitation:
    checked_at = now or timezone.now()
    invitation = (
        AccountInvitation.objects.select_related("student")
        .filter(token_hash=hash_invitation_token(token))
        .first()
    )
    if invitation is None:
        raise ValidationError({"invitation": "Invitation link is invalid."})
    _validate_invitation_pending(invitation, now=checked_at)
    return invitation


def _validate_invitation_pending(
    invitation: AccountInvitation,
    *,
    now,
) -> None:
    if invitation.accepted_at is not None:
        raise ValidationError({"invitation": "Invitation has already been used."})
    if invitation.revoked_at is not None:
        raise ValidationError({"invitation": "Invitation has been revoked."})
    if invitation.expires_at <= now:
        raise ValidationError({"invitation": "Invitation has expired."})


@transaction.atomic
def create_account_invitation(
    *,
    kind: str,
    actor: User,
    student_id: UUID | None = None,
    student_access_role: str = "",
    coach_display_name: str = "",
    expires_at=None,
) -> CreatedInvitation:
    require_permission(
        actor,
        "accounts.add_accountinvitation",
        "Account invitation creation permission is required.",
    )
    if kind not in AccountInvitation.Kind.values:
        raise ValidationError({"kind": "Unsupported invitation kind."})

    now = timezone.now()
    expires_at = expires_at or now + _default_invitation_ttl()
    if expires_at <= now:
        raise ValidationError({"expires_at": "Invitation must expire in the future."})

    student = None
    cleaned_role = ""
    cleaned_coach_name = ""

    if kind == AccountInvitation.Kind.STUDENT_ACCESS:
        if student_id is None:
            raise ValidationError({"student": "Student is required."})
        if student_access_role not in StudentAccess.Role.values:
            raise ValidationError({"student_access_role": "Student access role is required."})
        student = Student.objects.select_for_update().get(pk=student_id)
        if not student.is_active:
            raise ValidationError({"student": "Cannot invite access to an inactive student."})
        cleaned_role = student_access_role
    else:
        cleaned_coach_name = coach_display_name.strip()
        if not cleaned_coach_name:
            raise ValidationError({"coach_display_name": "Coach display name is required."})

    token = secrets.token_urlsafe(32)
    invitation = AccountInvitation.objects.create(
        kind=kind,
        token_hash=hash_invitation_token(token),
        student=student,
        student_access_role=cleaned_role,
        coach_display_name=cleaned_coach_name,
        expires_at=expires_at,
        created_by=actor,
    )
    record_event(
        event_type="AccountInvitationCreated",
        aggregate_type="AccountInvitation",
        aggregate_id=invitation.id,
        actor=actor,
        payload={
            "kind": invitation.kind,
            "student_id": str(student.id) if student is not None else None,
            "student_access_role": invitation.student_access_role,
            "coach_display_name": invitation.coach_display_name,
            "expires_at": invitation.expires_at.isoformat(),
        },
    )
    return CreatedInvitation(invitation=invitation, token=token)


@transaction.atomic
def revoke_account_invitation(
    *,
    invitation_id: UUID,
    actor: User,
    now=None,
) -> AccountInvitation:
    require_permission(
        actor,
        "accounts.change_accountinvitation",
        "Account invitation change permission is required.",
    )
    invitation = AccountInvitation.objects.select_for_update().get(pk=invitation_id)
    if invitation.accepted_at is not None:
        raise ValidationError({"invitation": "Accepted invitation cannot be revoked."})
    if invitation.revoked_at is not None:
        return invitation
    revoked_at = now or timezone.now()
    invitation.revoked_at = revoked_at
    invitation.revoked_by = actor
    invitation.save(update_fields=["revoked_at", "revoked_by"])
    record_event(
        event_type="AccountInvitationRevoked",
        aggregate_type="AccountInvitation",
        aggregate_id=invitation.id,
        actor=actor,
        payload={"revoked_at": revoked_at.isoformat()},
    )
    return invitation


def _technical_username() -> str:
    return f"u_{uuid.uuid4().hex}"


def _accept_invitation_locked(
    *,
    invitation: AccountInvitation,
    user: User,
    accepted_at,
) -> None:
    _validate_invitation_pending(invitation, now=accepted_at)

    if invitation.kind == AccountInvitation.Kind.STUDENT_ACCESS:
        student = Student.objects.select_for_update().get(pk=invitation.student_id)
        if not student.is_active:
            raise ValidationError(
                {"invitation": "The invited student is inactive."}
            )
        access = (
            StudentAccess.objects.select_for_update()
            .filter(user=user, student=student)
            .first()
        )
        if access is None:
            access = StudentAccess.objects.create(
                user=user,
                student=student,
                role=invitation.student_access_role,
                is_active=True,
            )
        else:
            access.role = invitation.student_access_role
            access.is_active = True
            access.save(update_fields=["role", "is_active"])
        target_id = access.id
        target_type = "StudentAccess"
    else:
        if CoachProfile.objects.select_for_update().filter(user=user).exists():
            raise ValidationError(
                {"invitation": "This account already has a coach profile."}
            )
        coach = CoachProfile.objects.create(
            user=user,
            display_name=invitation.coach_display_name,
            is_active=True,
        )
        target_id = coach.id
        target_type = "CoachProfile"

    invitation.accepted_at = accepted_at
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by"])
    record_event(
        event_type="AccountInvitationAccepted",
        aggregate_type="AccountInvitation",
        aggregate_id=invitation.id,
        actor=user,
        payload={
            "kind": invitation.kind,
            "target_type": target_type,
            "target_id": str(target_id),
        },
    )


@transaction.atomic
def authenticate_external_identity(
    *,
    provider: str,
    provider_subject: str,
    invitation_id: UUID | None = None,
    now=None,
) -> User:
    if provider not in ExternalIdentity.Provider.values:
        raise ValidationError({"provider": "Unsupported external identity provider."})
    subject = str(provider_subject).strip()
    if not subject:
        raise ValidationError({"provider_subject": "Provider subject is required."})

    authenticated_at = now or timezone.now()
    identity = (
        ExternalIdentity.objects.select_for_update()
        .select_related("user")
        .filter(provider=provider, provider_subject=subject)
        .first()
    )

    if identity is not None:
        user = identity.user
        if not user.is_active:
            raise ValidationError({"user": "This account is inactive."})
        if invitation_id is not None:
            invitation = AccountInvitation.objects.select_for_update().get(
                pk=invitation_id
            )
            _accept_invitation_locked(
                invitation=invitation,
                user=user,
                accepted_at=authenticated_at,
            )
        identity.last_used_at = authenticated_at
        identity.save(update_fields=["last_used_at"])
        return user

    if invitation_id is None:
        raise ValidationError(
            {
                "external_identity": (
                    "This external account is not linked to the school. "
                    "Use an invitation link first."
                )
            }
        )

    invitation = AccountInvitation.objects.select_for_update().get(pk=invitation_id)
    _validate_invitation_pending(invitation, now=authenticated_at)

    # Re-check after locking the invitation. Another invitation for the same
    # provider subject may have completed between the first lookup and now.
    identity = (
        ExternalIdentity.objects.select_for_update()
        .select_related("user")
        .filter(provider=provider, provider_subject=subject)
        .first()
    )
    created_identity = False
    if identity is not None:
        user = identity.user
        if not user.is_active:
            raise ValidationError({"user": "This account is inactive."})
    else:
        try:
            with transaction.atomic():
                user = User(username=_technical_username(), is_active=True)
                user.set_unusable_password()
                user.save()
                identity = ExternalIdentity.objects.create(
                    user=user,
                    provider=provider,
                    provider_subject=subject,
                    last_used_at=authenticated_at,
                )
                created_identity = True
        except IntegrityError:
            identity = (
                ExternalIdentity.objects.select_for_update()
                .select_related("user")
                .get(provider=provider, provider_subject=subject)
            )
            user = identity.user
            if not user.is_active:
                raise ValidationError({"user": "This account is inactive."})

    _accept_invitation_locked(
        invitation=invitation,
        user=user,
        accepted_at=authenticated_at,
    )
    if not created_identity:
        identity.last_used_at = authenticated_at
        identity.save(update_fields=["last_used_at"])
    else:
        record_event(
            event_type="ExternalIdentityLinked",
            aggregate_type="ExternalIdentity",
            aggregate_id=identity.id,
            actor=user,
            payload={
                "provider": provider,
                "user_id": str(user.id),
                "source": "invitation",
            },
        )
    return user


@transaction.atomic
def link_external_identity(
    *,
    user: User,
    provider: str,
    provider_subject: str,
    now=None,
) -> ExternalIdentity:
    if provider not in ExternalIdentity.Provider.values:
        raise ValidationError({"provider": "Unsupported external identity provider."})
    if not user.is_active:
        raise ValidationError({"user": "Inactive accounts cannot link providers."})

    linked_at = now or timezone.now()
    subject = str(provider_subject).strip()
    locked_user = User.objects.select_for_update().get(pk=user.id)
    existing_subject = (
        ExternalIdentity.objects.select_for_update()
        .filter(provider=provider, provider_subject=subject)
        .first()
    )
    if existing_subject is not None:
        if existing_subject.user_id != user.id:
            raise ValidationError(
                {"external_identity": "This external account is linked to another user."}
            )
        existing_subject.last_used_at = linked_at
        existing_subject.save(update_fields=["last_used_at"])
        return existing_subject

    existing_provider = (
        ExternalIdentity.objects.select_for_update()
        .filter(user=locked_user, provider=provider)
        .first()
    )
    if existing_provider is not None:
        raise ValidationError(
            {"external_identity": "This provider is already linked to the account."}
        )

    try:
        with transaction.atomic():
            identity = ExternalIdentity.objects.create(
                user=locked_user,
                provider=provider,
                provider_subject=subject,
                last_used_at=linked_at,
            )
    except IntegrityError as exc:
        conflict = ExternalIdentity.objects.filter(
            provider=provider,
            provider_subject=subject,
        ).first()
        if conflict is not None and conflict.user_id == locked_user.id:
            conflict.last_used_at = linked_at
            conflict.save(update_fields=["last_used_at"])
            return conflict
        raise ValidationError(
            {"external_identity": "This external account is linked to another user."}
        ) from exc
    record_event(
        event_type="ExternalIdentityLinked",
        aggregate_type="ExternalIdentity",
        aggregate_id=identity.id,
        actor=user,
        payload={
            "provider": provider,
            "user_id": str(locked_user.id),
            "source": "account_link",
        },
    )
    return identity
