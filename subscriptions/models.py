from django.conf import settings
from django.db import models

from accounts.models import Student
from attendance.models import AbsenceJustification, Attendance
from core.choices import SubscriptionCategory
from core.models import TimeStampedModel, UUIDModel
from scheduling.models import Lesson


class SubscriptionPlan(UUIDModel, TimeStampedModel):
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    validity_months = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(validity_months=1), name="subscription_plan_one_month")
        ]
        indexes = [models.Index(fields=["is_active", "name"], name="subplan_active_name_ix")]

    def __str__(self) -> str:
        return self.name


class SubscriptionPlanAllowance(UUIDModel):
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="allowances")
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    visit_limit = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["plan", "category"], name="subplan_allow_category_uq"),
            models.CheckConstraint(condition=models.Q(visit_limit__gt=0), name="subplan_allow_limit_gt0"),
        ]
        indexes = [models.Index(fields=["category", "visit_limit"], name="subplan_allow_lookup_ix")]


class Subscription(UUIDModel):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    plan_code_snapshot = models.CharField(max_length=64)
    plan_name_snapshot = models.CharField(max_length=128)
    valid_from = models.DateField()
    valid_until = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_until__gte=models.F("valid_from")),
                name="subscription_until_gte_from",
            ),
            models.CheckConstraint(
                condition=models.Q(cancelled_at__isnull=False) | models.Q(cancelled_by__isnull=True),
                name="subscription_cancel_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "valid_from", "valid_until"], name="subscription_student_dates_idx"),
            models.Index(fields=["student", "-valid_from"], name="subscription_history_ix"),
        ]


class SubscriptionAllowance(UUIDModel):
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="allowances")
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    visit_limit_snapshot = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "category"],
                name="suballow_category_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(visit_limit_snapshot__gt=0),
                name="suballow_limit_gt0",
            ),
        ]
        indexes = [models.Index(fields=["subscription", "category"], name="suballow_lookup_ix")]


class OneTimeEntitlement(UUIDModel):
    class Type(models.TextChoices):
        SINGLE_ICE = "single_ice", "Single ICE"
        SINGLE_HALL = "single_hall", "Single HALL"
        INDIVIDUAL_ICE = "individual_ice", "Individual ICE"
        MINI_GROUP_ICE = "mini_group_ice", "Mini-group ICE"
        TRIAL_ICE = "trial_ice", "Trial ICE"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="one_time_entitlements")
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="one_time_entitlements")
    entitlement_type = models.CharField(max_length=24, choices=Type.choices)
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(entitlement_type="single_ice", category=SubscriptionCategory.ICE)
                    | models.Q(entitlement_type="single_hall", category=SubscriptionCategory.HALL)
                    | models.Q(entitlement_type="individual_ice", category=SubscriptionCategory.ICE)
                    | models.Q(entitlement_type="mini_group_ice", category=SubscriptionCategory.ICE)
                    | models.Q(entitlement_type="trial_ice", category=SubscriptionCategory.ICE)
                ),
                name="onetime_type_category_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(cancelled_at__isnull=False) | models.Q(cancelled_by__isnull=True),
                name="onetime_cancel_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "lesson", "category"], name="onetime_lesson_lookup_ix"),
            models.Index(fields=["lesson", "cancelled_at"], name="onetime_active_ix"),
        ]


class MakeupEntitlement(UUIDModel):
    class Reason(models.TextChoices):
        MEDICAL_VERIFIED = "medical", "Verified medical absence"
        SCHOOL_RESCHEDULE = "school_reschedule", "School reschedule"
        ADMINISTRATIVE = "administrative", "Administrative"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="makeup_entitlements")
    source_lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="generated_makeup_entitlements")
    source_subscription_allowance = models.ForeignKey(
        SubscriptionAllowance,
        on_delete=models.PROTECT,
        related_name="makeup_entitlements",
    )
    source_justification = models.ForeignKey(
        AbsenceJustification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="makeup_entitlements",
    )
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    reason = models.CharField(max_length=24, choices=Reason.choices)
    valid_from = models.DateField()
    valid_until = models.DateField()
    target_lesson = models.ForeignKey(
        Lesson,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="targeted_makeup_entitlements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_until__gte=models.F("valid_from")),
                name="makeup_until_gte_from",
            ),
            models.UniqueConstraint(
                fields=["student", "source_lesson", "reason"],
                condition=models.Q(cancelled_at__isnull=True),
                name="makeup_active_student_lesson_uq",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason="medical")
                | models.Q(source_justification__isnull=False),
                name="makeup_medical_justif_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(reason="school_reschedule")
                | models.Q(target_lesson__isnull=False),
                name="makeup_resched_target_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(cancelled_at__isnull=False) | models.Q(cancelled_by__isnull=True),
                name="makeup_cancel_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "category", "valid_until"],
                condition=models.Q(cancelled_at__isnull=True),
                name="makeup_available_ix",
            )
        ]


class AttendanceCoverage(UUIDModel):
    attendance = models.ForeignKey(Attendance, on_delete=models.PROTECT, related_name="coverages")
    subscription_allowance = models.ForeignKey(
        SubscriptionAllowance,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="coverages",
    )
    one_time_entitlement = models.ForeignKey(
        OneTimeEntitlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="coverages",
    )
    makeup_entitlement = models.ForeignKey(
        MakeupEntitlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="coverages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attendance"],
                condition=models.Q(reversed_at__isnull=True),
                name="coverage_attendance_active_uq",
            ),
            models.UniqueConstraint(
                fields=["one_time_entitlement"],
                condition=models.Q(one_time_entitlement__isnull=False, reversed_at__isnull=True),
                name="coverage_onetime_active_uq",
            ),
            models.UniqueConstraint(
                fields=["makeup_entitlement"],
                condition=models.Q(makeup_entitlement__isnull=False, reversed_at__isnull=True),
                name="coverage_one_active_per_makeup",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(subscription_allowance__isnull=False, one_time_entitlement__isnull=True)
                    | models.Q(subscription_allowance__isnull=True, one_time_entitlement__isnull=False)
                ),
                name="coverage_primary_source_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(makeup_entitlement__isnull=True)
                | models.Q(subscription_allowance__isnull=False),
                name="coverage_makeup_allow_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(reversed_at__isnull=False) | models.Q(reversed_by__isnull=True),
                name="coverage_reverse_consistency",
            ),
        ]
        indexes = [
            models.Index(fields=["subscription_allowance", "reversed_at"], name="coverage_allowance_reverse_idx")
        ]


class SubscriptionLedgerEntry(UUIDModel):
    class EntryType(models.TextChoices):
        GRANT = "grant", "Grant"
        CONSUME = "consume", "Consume"
        RESTORE = "restore", "Restore"
        ADJUSTMENT = "adjustment", "Adjustment"

    allowance = models.ForeignKey(
        SubscriptionAllowance,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    coverage = models.ForeignKey(
        AttendanceCoverage,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=16, choices=EntryType.choices)
    delta = models.SmallIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(entry_type="grant", delta__gt=0)
                    | models.Q(entry_type="consume", delta=-1)
                    | models.Q(entry_type="restore", delta=1)
                    | (models.Q(entry_type="adjustment") & ~models.Q(delta=0))
                ),
                name="ledger_delta_matches_type",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(entry_type__in=["consume", "restore"]) & models.Q(coverage__isnull=False))
                    | (models.Q(entry_type__in=["grant", "adjustment"]) & models.Q(coverage__isnull=True))
                ),
                name="ledger_coverage_requirement",
            ),
            models.UniqueConstraint(
                fields=["allowance"],
                condition=models.Q(entry_type="grant"),
                name="ledger_one_grant_per_allowance",
            ),
            models.UniqueConstraint(
                fields=["coverage", "entry_type"],
                condition=models.Q(coverage__isnull=False),
                name="ledger_coverage_type_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["allowance", "created_at"], name="ledger_allowance_time_idx"),
            models.Index(fields=["allowance", "entry_type"], name="ledger_allowance_type_idx"),
        ]
