from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0005_absence_case_eligibility"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
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
        migrations.AddIndex(
            model_name="absencecompensationactiongrant",
            index=models.Index(
                fields=["case", "created_at"],
                name="absence_grant_case_created_ix",
            ),
        ),
    ]
