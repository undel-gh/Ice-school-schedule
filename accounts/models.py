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
            )
        ]
        indexes = [models.Index(fields=["user", "provider"], name="extid_user_provider_ix")]


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
