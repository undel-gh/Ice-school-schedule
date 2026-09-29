import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0002_medical_history_constraints"),
        ("subscriptions", "0003_absence_compensation_policy"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
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
                        choices=[("ice", "Ice"), ("hall", "Hall")],
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
    ]
