import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0002_medical_history_constraints"),
        ("subscriptions", "0002_makeup_active_unique"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
migrations.CreateModel(
            name="AbsenceCompensationPolicy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.SlugField(max_length=64)),
                ("version", models.PositiveSmallIntegerField()),
                ("name", models.CharField(max_length=128)),
                (
                    "absence_reason",
                    models.CharField(
                        choices=[
                            ("medical", "Medical"),
                            ("unexcused", "Unexcused"),
                            ("other", "Other"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "justification_requirement",
                    models.CharField(
                        choices=[
                            ("none", "No justification required"),
                            (
                                "verified_medical",
                                "Verified medical justification",
                            ),
                        ],
                        default="none",
                        max_length=32,
                    ),
                ),
                (
                    "max_eligible_absences",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "limit_scope",
                    models.CharField(
                        choices=[
                            ("student_period", "Student + period"),
                            (
                                "category_period",
                                "Student + category + period",
                            ),
                            (
                                "lesson_type_period",
                                "Student + lesson type + period",
                            ),
                        ],
                        default="student_period",
                        max_length=32,
                    ),
                ),
                ("effective_from", models.DateField()),
                (
                    "effective_until",
                    models.DateField(blank=True, null=True),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name="AbsenceCompensationPolicyAction",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("free_makeup", "Free makeup"),
                            (
                                "paid_makeup",
                                "Paid/deferred makeup",
                            ),
                            (
                                "billing_recalculation",
                                "Billing recalculation",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "target_period_rule",
                    models.CharField(
                        choices=[
                            ("current_period", "Current period"),
                            (
                                "next_student_period",
                                "Next student period",
                            ),
                            (
                                "explicit_target_window",
                                "Explicit target window",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "requirement",
                    models.CharField(
                        choices=[
                            ("none", "No additional requirement"),
                            ("fee_required", "Fee required"),
                            (
                                "target_subscription_required",
                                "Target subscription required",
                            ),
                            (
                                "fee_and_target_subscription_required",
                                "Fee and target subscription required",
                            ),
                        ],
                        default="none",
                        max_length=48,
                    ),
                ),
                (
                    "validity_days",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "priority",
                    models.PositiveSmallIntegerField(default=100),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actions",
                        to="subscriptions.absencecompensationpolicy",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AbsenceCompensationPolicyWindow",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                ("source_from", models.DateField()),
                ("source_until", models.DateField()),
                ("target_from", models.DateField()),
                ("target_until", models.DateField()),
                (
                    "requirement_override",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("none", "No additional requirement"),
                            ("fee_required", "Fee required"),
                            (
                                "target_subscription_required",
                                "Target subscription required",
                            ),
                            (
                                "fee_and_target_subscription_required",
                                "Fee and target subscription required",
                            ),
                        ],
                        default="",
                        max_length=48,
                    ),
                ),
                (
                    "priority",
                    models.PositiveSmallIntegerField(default=100),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "policy_action",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="windows",
                        to="subscriptions.absencecompensationpolicyaction",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicy",
            constraint=models.UniqueConstraint(
                fields=("code", "version"),
                name="absence_policy_code_version_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(("version__gt", 0)),
                name="absence_policy_version_gt0",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicy",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("max_eligible_absences__isnull", True))
                    | models.Q(("max_eligible_absences__gt", 0))
                ),
                name="absence_policy_max_gt0",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicy",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("effective_until__isnull", True))
                    | models.Q(
                        ("effective_until__gte", models.F("effective_from"))
                    )
                ),
                name="absence_policy_dates_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationpolicy",
            index=models.Index(
                fields=["absence_reason", "is_active", "effective_from"],
                name="absence_policy_lookup_ix",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationpolicy",
            index=models.Index(
                fields=["code", "-version"],
                name="absence_policy_version_ix",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicyaction",
            constraint=models.UniqueConstraint(
                fields=("policy", "action_type"),
                name="absence_policy_action_type_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicyaction",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("validity_days__isnull", True))
                    | models.Q(("validity_days__gt", 0))
                ),
                name="absence_action_validity_gt0",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicyaction",
            constraint=models.CheckConstraint(
                condition=models.Q(("priority__gt", 0)),
                name="absence_action_priority_gt0",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationpolicyaction",
            index=models.Index(
                fields=["policy", "is_active", "priority"],
                name="absence_action_lookup_ix",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicywindow",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source_until__gte", models.F("source_from"))
                ),
                name="absence_window_source_dates_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicywindow",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("target_until__gte", models.F("target_from"))
                ),
                name="absence_window_target_dates_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationpolicywindow",
            constraint=models.CheckConstraint(
                condition=models.Q(("priority__gt", 0)),
                name="absence_window_priority_gt0",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationpolicywindow",
            index=models.Index(
                fields=[
                    "policy_action",
                    "is_active",
                    "source_from",
                    "source_until",
                ],
                name="absence_window_lookup_ix",
            ),
        ),

migrations.CreateModel(
            name="AbsenceCompensationCase",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "absence_reason",
                    models.CharField(
                        choices=[
                            ("medical", "Medical"),
                            ("unexcused", "Unexcused"),
                            ("other", "Other"),
                        ],
                        max_length=24,
                    ),
                ),
                ("source_date", models.DateField()),
                (
                    "category",
                    models.CharField(
                        choices=[("ice", "ICE"), ("hall", "HALL")],
                        max_length=16,
                    ),
                ),
                ("policy_code_snapshot", models.SlugField(max_length=64)),
                (
                    "policy_version_snapshot",
                    models.PositiveSmallIntegerField(),
                ),
                ("policy_name_snapshot", models.CharField(max_length=128)),
                (
                    "justification_requirement_snapshot",
                    models.CharField(max_length=32),
                ),
                (
                    "max_eligible_absences_snapshot",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        null=True,
                    ),
                ),
                ("limit_scope_snapshot", models.CharField(max_length=32)),
                ("actions_snapshot", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="open",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cancelled_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "attendance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensation_cases",
                        to="attendance.attendance",
                    ),
                ),
                (
                    "cancelled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensation_cases",
                        to="subscriptions.absencecompensationpolicy",
                    ),
                ),
                (
                    "source_justification",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensation_cases",
                        to="attendance.absencejustification",
                    ),
                ),
                (
                    "source_lesson",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="absence_compensation_cases",
                        to="scheduling.lesson",
                    ),
                ),
                (
                    "source_subscription_allowance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="absence_compensation_cases",
                        to="subscriptions.subscriptionallowance",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="absence_compensation_cases",
                        to="accounts.student",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "open")),
                fields=("attendance",),
                name="absence_case_one_open_attendance_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.CheckConstraint(
                condition=models.Q(("policy_version_snapshot__gt", 0)),
                name="absence_case_policy_version_gt0",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("cancelled_at__isnull", True),
                        ("cancelled_by__isnull", True),
                        ("status", "open"),
                    )
                    | models.Q(
                        ("cancelled_at__isnull", False),
                        ("status", "cancelled"),
                    )
                ),
                name="absence_case_status_cancel_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationcase",
            index=models.Index(
                fields=["student", "source_date", "status"],
                name="absence_case_student_date_ix",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationcase",
            index=models.Index(
                fields=["policy", "status"],
                name="absence_case_policy_status_ix",
            ),
        ),

migrations.AddField(
            model_name="absencecompensationcase",
            name="eligibility_status",
            field=models.CharField(
                choices=[
                    ("eligible", "Eligible"),
                    ("limit_exceeded", "Limit exceeded"),
                    ("undetermined", "Undetermined"),
                ],
                default="undetermined",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="eligible_absence_ordinal",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="eligibility_period_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="eligibility_period_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="eligibility_evaluated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.CheckConstraint(
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
        ),

migrations.RemoveConstraint(
            model_name="absencecompensationcase",
            name="absence_case_one_open_attendance_uq",
        ),
        migrations.RemoveConstraint(
            model_name="absencecompensationcase",
            name="absence_case_status_cancel_ck",
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="materialized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="materialized_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="reversed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="reversed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="absencecompensationcase",
            name="reversal_reason",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="absencecompensationcase",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("materialized", "Materialized"),
                    ("reversed", "Reversed"),
                    ("cancelled", "Cancelled"),
                ],
                default="open",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="makeupentitlement",
            name="reason",
            field=models.CharField(
                choices=[
                    ("medical", "Verified medical absence"),
                    ("school_reschedule", "School reschedule"),
                    ("administrative", "Administrative"),
                    (
                        "absence_compensation",
                        "Absence compensation",
                    ),
                ],
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="makeupentitlement",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    cancelled_at__isnull=True,
                    reason__in=[
                        "medical",
                        "absence_compensation",
                    ],
                ),
                fields=("student", "source_lesson"),
                name="makeup_active_absence_source_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("status__in", ["open", "materialized"])
                ),
                fields=("attendance",),
                name="absence_case_one_active_attendance_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationcase",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="open",
                        materialized_at__isnull=True,
                        materialized_by__isnull=True,
                        reversed_at__isnull=True,
                        reversed_by__isnull=True,
                        reversal_reason="",
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                    )
                    | models.Q(
                        status="materialized",
                        materialized_at__isnull=False,
                        reversed_at__isnull=True,
                        reversed_by__isnull=True,
                        reversal_reason="",
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                    )
                    | models.Q(
                        status="reversed",
                        materialized_at__isnull=False,
                        reversed_at__isnull=False,
                        reversal_reason__gt="",
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                    )
                    | models.Q(
                        status="cancelled",
                        materialized_at__isnull=True,
                        reversed_at__isnull=True,
                        reversal_reason="",
                        cancelled_at__isnull=False,
                    )
                ),
                name="absence_case_status_ck",
            ),
        ),
        migrations.CreateModel(
            name="AbsenceCompensationActionGrant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("free_makeup", "Free makeup"),
                            (
                                "paid_makeup",
                                "Paid/deferred makeup",
                            ),
                            (
                                "billing_recalculation",
                                "Billing recalculation",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                ("action_snapshot", models.JSONField(default=dict)),
                (
                    "fee_confirmed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "fee_confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "activated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "refund_required",
                    models.BooleanField(blank=True, null=True),
                ),
                (
                    "reversed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "reversed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reversal_reason",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="action_grants",
                        to="subscriptions.absencecompensationcase",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "makeup_entitlement",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensation_action_grant",
                        to="subscriptions.makeupentitlement",
                    ),
                ),
                (
                    "target_subscription",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensation_action_grants",
                        to="subscriptions.subscription",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.UniqueConstraint(
                fields=("case", "action_type"),
                name="absence_action_grant_type_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(("action_type", "free_makeup"))
                    | models.Q(("makeup_entitlement__isnull", False))
                ),
                name="absence_grant_free_makeup_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        fee_confirmed_at__isnull=True,
                        fee_confirmed_by__isnull=True,
                    )
                    | models.Q(
                        fee_confirmed_at__isnull=False,
                        fee_confirmed_by__isnull=False,
                    )
                ),
                name="absence_grant_fee_confirm_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        activated_at__isnull=True,
                        makeup_entitlement__isnull=True,
                    )
                    | models.Q(
                        activated_at__isnull=False,
                        makeup_entitlement__isnull=False,
                    )
                ),
                name="absence_grant_activation_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(
                        action_type="paid_makeup",
                        activated_at__isnull=False,
                    )
                    | models.Q(fee_confirmed_at__isnull=False)
                ),
                name="absence_grant_paid_fee_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        reversed_at__isnull=True,
                        reversed_by__isnull=True,
                        reversal_reason="",
                    )
                    | models.Q(
                        reversed_at__isnull=False,
                        reversal_reason__gt="",
                    )
                ),
                name="absence_grant_reversal_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencecompensationactiongrant",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(refund_required__isnull=True)
                        & ~models.Q(
                            action_type="paid_makeup",
                            fee_confirmed_at__isnull=False,
                            reversed_at__isnull=False,
                        )
                    )
                    | models.Q(
                        refund_required__isnull=False,
                        action_type="paid_makeup",
                        fee_confirmed_at__isnull=False,
                        reversed_at__isnull=False,
                    )
                ),
                name="absence_grant_refund_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="absencecompensationactiongrant",
            index=models.Index(
                fields=["case", "created_at"],
                name="absence_grant_case_created_ix",
            ),
        ),
    ]
