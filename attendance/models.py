from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Student
from core.models import UUIDModel
from scheduling.models import Lesson


class Attendance(UUIDModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"

    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="attendance_records")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance_records")
    status = models.CharField(max_length=16, choices=Status.choices)
    marked_at = models.DateTimeField(default=timezone.now)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lesson", "student"], name="attendance_lesson_student_uniq")]
        indexes = [
            models.Index(fields=["lesson", "status"], name="attendance_lesson_status_idx"),
            models.Index(fields=["student", "marked_at"], name="attendance_student_marked_idx"),
        ]


class AbsenceJustification(UUIDModel):
    class Type(models.TextChoices):
        MEDICAL = "medical", "Medical"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        REVOKED = "revoked", "Revoked"

    class VerificationMethod(models.TextChoices):
        IN_PERSON = "in_person", "In person"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="absence_justifications")
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="absence_justifications")
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.MEDICAL)
    status = models.CharField(max_length=16, choices=Status.choices, default="pending")
    verification_method = models.CharField(max_length=16, choices=VerificationMethod.choices, default=VerificationMethod.IN_PERSON)

    declared_at = models.DateTimeField(default=timezone.now)
    declared_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "lesson", "type"],
                name="absence_student_lesson_uq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="pending", reviewed_at__isnull=True, revoked_at__isnull=True)
                    | models.Q(status="verified", reviewed_at__isnull=False, revoked_at__isnull=True)
                    | models.Q(status="rejected", reviewed_at__isnull=False, revoked_at__isnull=True)
                    | models.Q(status="revoked", reviewed_at__isnull=False, revoked_at__isnull=False)
                ),
                name="absence_state_metadata_ck",
            ),
        ]
        indexes = [models.Index(fields=["status", "declared_at"], name="absence_status_declared_ix")]
