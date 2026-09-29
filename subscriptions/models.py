from django.conf import settings
from django.core.exceptions import ValidationError
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


class AbsenceCompensationPolicy(UUIDModel, TimeStampedModel):
    class AbsenceReason(models.TextChoices):
        MEDICAL = "medical", "Medical"
        UNEXCUSED = "unexcused", "Unexcused"
        OTHER = "other", "Other"

    class JustificationRequirement(models.TextChoices):
        NONE = "none", "No justification required"
        VERIFIED_MEDICAL = "verified_medical", "Verified medical justification"

    class LimitScope(models.TextChoices):
        STUDENT_PERIOD = "student_period", "Student + period"
        CATEGORY_PERIOD = "category_period", "Student + category + period"
        LESSON_TYPE_PERIOD = (
            "lesson_type_period",
            "Student + lesson type + period",
        )

    code = models.SlugField(max_length=64)
    version = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=128)
    absence_reason = models.CharField(
        max_length=24,
        choices=AbsenceReason.choices,
    )
    justification_requirement = models.CharField(
        max_length=32,
        choices=JustificationRequirement.choices,
        default=JustificationRequirement.NONE,
    )
    max_eligible_absences = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    limit_scope = models.CharField(
        max_length=32,
        choices=LimitScope.choices,
        default=LimitScope.STUDENT_PERIOD,
    )
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["code", "version"],
                name="absence_policy_code_version_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(version__gt=0),
                name="absence_policy_version_gt0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(max_eligible_absences__isnull=True)
                    | models.Q(max_eligible_absences__gt=0)
                ),
                name="absence_policy_max_gt0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(effective_until__isnull=True)
                    | models.Q(effective_until__gte=models.F("effective_from"))
                ),
                name="absence_policy_dates_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["absence_reason", "is_active", "effective_from"],
                name="absence_policy_lookup_ix",
            ),
            models.Index(
                fields=["code", "-version"],
                name="absence_policy_version_ix",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not self.is_active or self.effective_from is None:
            return
        overlaps = AbsenceCompensationPolicy.objects.filter(
            absence_reason=self.absence_reason,
            is_active=True,
        ).exclude(pk=self.pk)
        overlaps = overlaps.filter(
            models.Q(effective_until__isnull=True)
            | models.Q(effective_until__gte=self.effective_from)
        )
        if self.effective_until is not None:
            overlaps = overlaps.filter(
                effective_from__lte=self.effective_until
            )
        if overlaps.exists():
            raise ValidationError(
                {
                    "effective_from": (
                        "Another active compensation policy for this "
                        "absence reason overlaps this effective interval."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.pk and AbsenceCompensationCase.objects.filter(
            policy_id=self.pk
        ).exists():
            previous = AbsenceCompensationPolicy.objects.get(pk=self.pk)
            immutable_fields = (
                "code",
                "version",
                "name",
                "absence_reason",
                "justification_requirement",
                "max_eligible_absences",
                "limit_scope",
                "effective_from",
                "effective_until",
                "is_active",
            )
            if any(
                getattr(previous, field) != getattr(self, field)
                for field in immutable_fields
            ):
                raise ValidationError(
                    "Referenced compensation policy versions are immutable."
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and AbsenceCompensationCase.objects.filter(
            policy_id=self.pk
        ).exists():
            raise ValidationError(
                "Referenced compensation policy versions cannot be deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class AbsenceCompensationPolicyAction(UUIDModel):
    class ActionType(models.TextChoices):
        FREE_MAKEUP = "free_makeup", "Free makeup"
        PAID_MAKEUP = "paid_makeup", "Paid/deferred makeup"
        BILLING_RECALCULATION = (
            "billing_recalculation",
            "Billing recalculation",
        )

    class TargetPeriodRule(models.TextChoices):
        CURRENT_PERIOD = "current_period", "Current period"
        NEXT_STUDENT_PERIOD = "next_student_period", "Next student period"
        EXPLICIT_TARGET_WINDOW = (
            "explicit_target_window",
            "Explicit target window",
        )

    class Requirement(models.TextChoices):
        NONE = "none", "No additional requirement"
        FEE_REQUIRED = "fee_required", "Fee required"
        TARGET_SUBSCRIPTION_REQUIRED = (
            "target_subscription_required",
            "Target subscription required",
        )
        FEE_AND_TARGET_SUBSCRIPTION_REQUIRED = (
            "fee_and_target_subscription_required",
            "Fee and target subscription required",
        )

    policy = models.ForeignKey(
        AbsenceCompensationPolicy,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    action_type = models.CharField(
        max_length=32,
        choices=ActionType.choices,
    )
    target_period_rule = models.CharField(
        max_length=32,
        choices=TargetPeriodRule.choices,
    )
    requirement = models.CharField(
        max_length=48,
        choices=Requirement.choices,
        default=Requirement.NONE,
    )
    validity_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    priority = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "action_type"],
                name="absence_policy_action_type_uq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(validity_days__isnull=True)
                    | models.Q(validity_days__gt=0)
                ),
                name="absence_action_validity_gt0",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__gt=0),
                name="absence_action_priority_gt0",
            ),
        ]
        indexes = [
            models.Index(
                fields=["policy", "is_active", "priority"],
                name="absence_action_lookup_ix",
            )
        ]

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = (
                AbsenceCompensationPolicyAction.objects.select_related(
                    "policy"
                )
                .filter(pk=self.pk)
                .first()
            )
        if previous is not None:
            if previous.policy.compensation_cases.exists():
                immutable_fields = (
                    "policy_id",
                    "action_type",
                    "target_period_rule",
                    "requirement",
                    "validity_days",
                    "priority",
                    "is_active",
                )
                if any(
                    getattr(previous, field) != getattr(self, field)
                    for field in immutable_fields
                ):
                    raise ValidationError(
                        "Actions of referenced compensation policies are "
                        "immutable."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.policy.compensation_cases.exists():
            raise ValidationError(
                "Actions of referenced compensation policies cannot be "
                "deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.policy} / {self.action_type}"


class AbsenceCompensationPolicyWindow(UUIDModel):
    policy_action = models.ForeignKey(
        AbsenceCompensationPolicyAction,
        on_delete=models.CASCADE,
        related_name="windows",
    )
    name = models.CharField(max_length=128)
    source_from = models.DateField()
    source_until = models.DateField()
    target_from = models.DateField()
    target_until = models.DateField()
    requirement_override = models.CharField(
        max_length=48,
        choices=AbsenceCompensationPolicyAction.Requirement.choices,
        blank=True,
        default="",
    )
    priority = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source_until__gte=models.F("source_from")),
                name="absence_window_source_dates_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(target_until__gte=models.F("target_from")),
                name="absence_window_target_dates_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__gt=0),
                name="absence_window_priority_gt0",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "policy_action",
                    "is_active",
                    "source_from",
                    "source_until",
                ],
                name="absence_window_lookup_ix",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if (
            not self.is_active
            or self.policy_action_id is None
            or self.source_from is None
            or self.source_until is None
        ):
            return
        overlaps = AbsenceCompensationPolicyWindow.objects.filter(
            policy_action_id=self.policy_action_id,
            is_active=True,
            priority=self.priority,
            source_from__lte=self.source_until,
            source_until__gte=self.source_from,
        ).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError(
                {
                    "source_from": (
                        "Another active window with the same priority "
                        "overlaps this source interval."
                    )
                }
            )

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = (
                AbsenceCompensationPolicyWindow.objects.select_related(
                    "policy_action__policy"
                )
                .filter(pk=self.pk)
                .first()
            )
        if previous is not None:
            if previous.policy_action.policy.compensation_cases.exists():
                immutable_fields = (
                    "policy_action_id",
                    "name",
                    "source_from",
                    "source_until",
                    "target_from",
                    "target_until",
                    "requirement_override",
                    "priority",
                    "is_active",
                )
                if any(
                    getattr(previous, field) != getattr(self, field)
                    for field in immutable_fields
                ):
                    raise ValidationError(
                        "Windows of referenced compensation policies are "
                        "immutable."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if (
            self.pk
            and self.policy_action.policy.compensation_cases.exists()
        ):
            raise ValidationError(
                "Windows of referenced compensation policies cannot be "
                "deleted."
            )
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class AbsenceCompensationCase(UUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CANCELLED = "cancelled", "Cancelled"

    class EligibilityStatus(models.TextChoices):
        ELIGIBLE = "eligible", "Eligible"
        LIMIT_EXCEEDED = "limit_exceeded", "Limit exceeded"
        UNDETERMINED = "undetermined", "Undetermined"

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.PROTECT,
        related_name="compensation_cases",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="absence_compensation_cases",
    )
    source_lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="absence_compensation_cases",
    )
    source_subscription_allowance = models.ForeignKey(
        "SubscriptionAllowance",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="absence_compensation_cases",
    )
    source_justification = models.ForeignKey(
        AbsenceJustification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="compensation_cases",
    )
    policy = models.ForeignKey(
        AbsenceCompensationPolicy,
        on_delete=models.PROTECT,
        related_name="compensation_cases",
    )

    absence_reason = models.CharField(
        max_length=24,
        choices=AbsenceCompensationPolicy.AbsenceReason.choices,
    )
    source_date = models.DateField()
    category = models.CharField(
        max_length=16,
        choices=SubscriptionCategory.choices,
    )

    policy_code_snapshot = models.SlugField(max_length=64)
    policy_version_snapshot = models.PositiveSmallIntegerField()
    policy_name_snapshot = models.CharField(max_length=128)
    justification_requirement_snapshot = models.CharField(max_length=32)
    max_eligible_absences_snapshot = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    limit_scope_snapshot = models.CharField(max_length=32)
    actions_snapshot = models.JSONField(default=list)

    eligibility_status = models.CharField(
        max_length=24,
        choices=EligibilityStatus.choices,
        default=EligibilityStatus.UNDETERMINED,
    )
    eligible_absence_ordinal = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    eligibility_period_from = models.DateField(null=True, blank=True)
    eligibility_period_until = models.DateField(null=True, blank=True)
    eligibility_evaluated_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attendance"],
                condition=models.Q(status="open"),
                name="absence_case_one_open_attendance_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(policy_version_snapshot__gt=0),
                name="absence_case_policy_version_gt0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        eligibility_status="undetermined",
                        eligible_absence_ordinal__isnull=True,
                        eligibility_period_from__isnull=True,
                        eligibility_period_until__isnull=True,
                    )
                    | models.Q(
                        eligibility_status="eligible",
                        max_eligible_absences_snapshot__isnull=True,
                        eligible_absence_ordinal__isnull=True,
                        eligibility_period_from__isnull=True,
                        eligibility_period_until__isnull=True,
                    )
                    | models.Q(
                        eligibility_status__in=[
                            "eligible",
                            "limit_exceeded",
                        ],
                        max_eligible_absences_snapshot__isnull=False,
                        eligible_absence_ordinal__isnull=False,
                        eligibility_period_from__isnull=False,
                        eligibility_period_until__isnull=False,
                        eligibility_period_until__gte=models.F(
                            "eligibility_period_from"
                        ),
                    )
                ),
                name="absence_case_eligibility_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="open",
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                    )
                    | models.Q(
                        status="cancelled",
                        cancelled_at__isnull=False,
                    )
                ),
                name="absence_case_status_cancel_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "source_date", "status"],
                name="absence_case_student_date_ix",
            ),
            models.Index(
                fields=["policy", "status"],
                name="absence_case_policy_status_ix",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.student} / {self.source_date} / "
            f"{self.absence_reason}"
        )


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
