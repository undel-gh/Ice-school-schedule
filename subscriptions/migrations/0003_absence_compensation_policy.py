import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0002_makeup_active_unique"),
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
    ]
