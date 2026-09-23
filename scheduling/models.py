from django.conf import settings
from django.db import models

from accounts.models import CoachProfile, Student
from core.choices import SubscriptionCategory
from core.models import TimeStampedModel, UUIDModel


class TrainingGroup(UUIDModel, TimeStampedModel):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    default_minimum_attendees = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(default_minimum_attendees__gte=1),
                name="training_group_minimum_gte_1",
            )
        ]
        indexes = [models.Index(fields=["is_active", "name"], name="training_group_active_name_idx")]

    def __str__(self) -> str:
        return self.name


class GroupMembership(UUIDModel):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="group_memberships")
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name="memberships")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=models.F("starts_on")),
                name="group_membership_end_gte_start",
            ),
            models.UniqueConstraint(
                fields=["student", "group"],
                condition=models.Q(ends_on__isnull=True),
                name="group_membership_one_open_uniq",
            ),
            models.UniqueConstraint(
                fields=["student", "group", "starts_on"],
                name="membership_student_start_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["group", "starts_on", "ends_on"], name="membership_group_dates_ix"),
            models.Index(fields=["student", "starts_on", "ends_on"], name="membership_student_dates_ix"),
        ]


class Venue(UUIDModel):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class LessonType(UUIDModel):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    subscription_category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class ScheduleTemplate(UUIDModel, TimeStampedModel):
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name="schedule_templates")
    lesson_type = models.ForeignKey(LessonType, on_delete=models.PROTECT, related_name="schedule_templates")
    coach = models.ForeignKey(CoachProfile, on_delete=models.PROTECT, related_name="schedule_templates")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="schedule_templates")
    weekday = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    duration_minutes = models.PositiveSmallIntegerField()
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    minimum_attendees_override = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(weekday__gte=0, weekday__lte=6), name="schedule_template_weekday_0_6"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gt=0), name="schedtpl_duration_gt0"),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=models.F("valid_from")),
                name="schedtpl_until_gte_from",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_attendees_override__isnull=True)
                | models.Q(minimum_attendees_override__gte=1),
                name="schedtpl_minimum_gte1",
            ),
        ]
        indexes = [
            models.Index(fields=["is_active", "weekday"], name="schedtpl_active_weekday_ix"),
            models.Index(fields=["group", "is_active"], name="schedtpl_group_active_ix"),
        ]


class Lesson(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RSVP_OPEN = "rsvp_open", "RSVP open"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class CancellationReason(models.TextChoices):
        LOW_ATTENDANCE = "low_attendance", "Low attendance"
        COACH_UNAVAILABLE = "coach_unavailable", "Coach unavailable"
        VENUE_UNAVAILABLE = "venue_unavailable", "Venue unavailable"
        ADMINISTRATIVE = "administrative", "Administrative"
        OTHER = "other", "Other"

    source_template = models.ForeignKey(
        ScheduleTemplate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="generated_lessons",
    )
    group = models.ForeignKey(TrainingGroup, on_delete=models.PROTECT, related_name="lessons")
    lesson_type = models.ForeignKey(LessonType, on_delete=models.PROTECT, related_name="lessons")
    coach = models.ForeignKey(CoachProfile, on_delete=models.PROTECT, related_name="lessons")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="lessons")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    minimum_attendees = models.PositiveSmallIntegerField()
    rsvp_deadline = models.DateTimeField()
    decision_deadline = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    published_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decision_evaluated_at = models.DateTimeField(null=True, blank=True)
    decision_yes_count = models.PositiveIntegerField(null=True, blank=True)
    decision_no_count = models.PositiveIntegerField(null=True, blank=True)
    decision_no_response_count = models.PositiveIntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    attendance_submitted_at = models.DateTimeField(null=True, blank=True)
    attendance_submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancellation_reason = models.CharField(max_length=32, choices=CancellationReason.choices, null=True, blank=True)
    replacement_lesson = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replaced_lesson",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="lesson_ends_after_start"),
            models.CheckConstraint(condition=models.Q(rsvp_deadline__lte=models.F("decision_deadline")), name="lesson_rsvp_before_decision"),
            models.CheckConstraint(condition=models.Q(decision_deadline__lte=models.F("starts_at")), name="lesson_decision_before_start"),
            models.CheckConstraint(condition=models.Q(minimum_attendees__gte=1), name="lesson_minimum_gte_1"),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="cancelled")
                    | (models.Q(cancelled_at__isnull=False) & models.Q(cancellation_reason__isnull=False))
                ),
                name="lesson_cancel_metadata_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(replacement_lesson__isnull=True) | models.Q(status="cancelled"),
                name="lesson_replace_cancelled_ck",
            ),
            models.UniqueConstraint(
                fields=["source_template", "starts_at"],
                condition=models.Q(source_template__isnull=False),
                name="lesson_template_start_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "starts_at"], name="lesson_status_start_idx"),
            models.Index(fields=["group", "starts_at"], name="lesson_group_start_idx"),
            models.Index(fields=["coach", "starts_at"], name="lesson_coach_start_idx"),
            models.Index(fields=["status", "decision_deadline"], name="lesson_status_decision_idx"),
        ]


class LessonEnrollment(UUIDModel):
    class Reason(models.TextChoices):
        MAKEUP = "makeup", "Make-up"
        GUEST = "guest", "Guest"
        ADMINISTRATIVE = "administrative", "Administrative"

    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="enrollments")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_enrollments")
    reason = models.CharField(max_length=24, choices=Reason.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "student"],
                condition=models.Q(cancelled_at__isnull=True),
                name="lesson_enroll_active_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(cancelled_at__isnull=False) | models.Q(cancelled_by__isnull=True),
                name="lesson_enroll_cancel_actor_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["lesson", "cancelled_at"], name="lesson_enroll_cancel_ix"),
            models.Index(fields=["student", "cancelled_at"], name="student_enroll_cancel_ix"),
        ]


class LessonRosterEntry(UUIDModel):
    class Source(models.TextChoices):
        GROUP = "group", "Group"
        ENROLLMENT = "enrollment", "Enrollment"
        MANUAL = "manual", "Manual"

    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="roster_entries")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_roster_entries")
    source = models.CharField(max_length=16, choices=Source.choices)
    group_membership = models.ForeignKey(GroupMembership, null=True, blank=True, on_delete=models.PROTECT, related_name="roster_entries")
    lesson_enrollment = models.ForeignKey(LessonEnrollment, null=True, blank=True, on_delete=models.PROTECT, related_name="roster_entries")
    is_active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["lesson", "student"], name="lesson_roster_student_uq"),
            models.CheckConstraint(
                condition=(
                    (models.Q(is_active=True) & models.Q(deactivated_at__isnull=True))
                    | (models.Q(is_active=False) & models.Q(deactivated_at__isnull=False))
                ),
                name="lesson_roster_active_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["lesson", "is_active"], name="lesson_roster_active_ix"),
            models.Index(fields=["student", "is_active"], name="student_roster_active_ix"),
        ]


class LessonResponse(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"

    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="responses")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_responses")
    status = models.CharField(max_length=8, choices=Status.choices)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lesson", "student"], name="lesson_response_student_uq")]
        indexes = [models.Index(fields=["lesson", "status"], name="lesson_response_status_ix")]
