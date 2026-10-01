import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import TimeStampedModel, UUIDModel


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(blank=True)


class ExternalIdentity(UUIDModel):
    class Provider(models.TextChoices):
        YANDEX = "yandex", "Yandex"
        VK = "vk", "VK"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="external_identities")
    provider = models.CharField(max_length=16, choices=Provider.choices)
    provider_subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_subject"],
                name="extid_provider_subject_uq",
            ),
            models.UniqueConstraint(
                fields=["user", "provider"],
                name="extid_user_provider_uq",
            ),
        ]
        indexes = [models.Index(fields=["user", "provider"], name="extid_user_provider_ix")]



class AccountInvitation(UUIDModel):
    class Kind(models.TextChoices):
        STUDENT_ACCESS = "student_access", "Student access"
        COACH = "coach", "Coach"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    student = models.ForeignKey(
        "Student",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="account_invitations",
    )
    student_access_role = models.CharField(
        max_length=16,
        choices=(
            ("self", "Self"),
            ("guardian", "Guardian"),
        ),
        blank=True,
        default="",
    )
    coach_display_name = models.CharField(max_length=100, blank=True, default="")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_account_invitations",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="accepted_account_invitations",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="revoked_account_invitations",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=["kind", "expires_at"],
                name="account_invite_kind_exp_ix",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="student_access",
                        student__isnull=False,
                        student_access_role__in=["self", "guardian"],
                        coach_display_name="",
                    )
                    | models.Q(
                        kind="coach",
                        student__isnull=True,
                        student_access_role="",
                    )
                ),
                name="account_invite_target_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(accepted_at__isnull=True, accepted_by__isnull=True)
                    | models.Q(accepted_at__isnull=False, accepted_by__isnull=False)
                ),
                name="account_invite_accept_pair_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(revoked_at__isnull=True, revoked_by__isnull=True)
                    | models.Q(revoked_at__isnull=False, revoked_by__isnull=False)
                ),
                name="account_invite_revoke_pair_ck",
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(accepted_at__isnull=False)
                    & models.Q(revoked_at__isnull=False)
                ),
                name="account_invite_terminal_ck",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} / {self.id}"


class Student(UUIDModel, TimeStampedModel):
    display_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["is_active", "display_name"], name="student_active_name_idx")]

    def __str__(self) -> str:
        return self.display_name


class StudentAccess(UUIDModel):
    class Role(models.TextChoices):
        SELF = "self", "Self"
        GUARDIAN = "guardian", "Guardian"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="student_accesses")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="accesses")
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "student"], name="student_access_user_uq")
        ]
        indexes = [
            models.Index(fields=["user", "is_active"], name="student_access_user_active_idx"),
            models.Index(fields=["student", "is_active"], name="student_access_active_ix"),
        ]


class CoachProfile(UUIDModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="coach_profile")
    display_name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.display_name
