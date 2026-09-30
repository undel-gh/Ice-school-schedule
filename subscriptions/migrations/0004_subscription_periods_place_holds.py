import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0003_absence_compensation_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPeriodScheme",
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
                ("code", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=128)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("calendar_month", "Calendar month"),
                            (
                                "rolling_28_first_lesson",
                                "28 days from first lesson",
                            ),
                            (
                                "fixed_28_days",
                                "Fixed school-wide 28-day periods",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "fixed_anchor_date",
                    models.DateField(blank=True, null=True),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="period_scheme",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="plans",
                to="subscriptions.subscriptionperiodscheme",
            ),
        ),
        migrations.CreateModel(
            name="SubscriptionPeriod",
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
                    "mode_snapshot",
                    models.CharField(
                        choices=[
                            ("calendar_month", "Calendar month"),
                            (
                                "rolling_28_first_lesson",
                                "28 days from first lesson",
                            ),
                            (
                                "fixed_28_days",
                                "Fixed school-wide 28-day periods",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "fixed_anchor_snapshot",
                    models.DateField(blank=True, null=True),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending activation"),
                            ("active", "Active"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("starts_on", models.DateField(blank=True, null=True)),
                ("ends_on", models.DateField(blank=True, null=True)),
                (
                    "activated_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "activation_lesson",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activated_subscription_periods",
                        to="scheduling.lesson",
                    ),
                ),
                (
                    "scheme",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="subscription_periods",
                        to="subscriptions.subscriptionperiodscheme",
                    ),
                ),
                (
                    "subscription",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="billing_period",
                        to="subscriptions.subscription",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="GroupPlaceHold",
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
                ("period_from", models.DateField()),
                ("period_until", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_payment", "Pending payment"),
                            ("active", "Active"),
                            ("cancelled", "Cancelled"),
                            ("expired", "Expired"),
                        ],
                        default="pending_payment",
                        max_length=24,
                    ),
                ),
                (
                    "fee_confirmed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cancellation_reason",
                    models.CharField(blank=True, default="", max_length=128),
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
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="place_holds",
                        to="scheduling.traininggroup",
                    ),
                ),
                (
                    "period_scheme",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="group_place_holds",
                        to="subscriptions.subscriptionperiodscheme",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="group_place_holds",
                        to="accounts.student",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="subscriptionperiodscheme",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        mode="fixed_28_days",
                        fixed_anchor_date__isnull=False,
                    )
                    | (
                        ~models.Q(mode="fixed_28_days")
                        & models.Q(fixed_anchor_date__isnull=True)
                    )
                ),
                name="subperiod_scheme_anchor_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="subscriptionperiodscheme",
            index=models.Index(
                fields=["is_active", "mode", "name"],
                name="subperiod_scheme_lookup_ix",
            ),
        ),
        migrations.AddConstraint(
            model_name="subscriptionperiod",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        state="pending",
                        starts_on__isnull=True,
                        ends_on__isnull=True,
                        activation_lesson__isnull=True,
                        activated_at__isnull=True,
                    )
                    | models.Q(
                        state="active",
                        starts_on__isnull=False,
                        ends_on__isnull=False,
                        activated_at__isnull=False,
                        ends_on__gte=models.F("starts_on"),
                    )
                ),
                name="subperiod_state_dates_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="subscriptionperiod",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        mode_snapshot="fixed_28_days",
                        fixed_anchor_snapshot__isnull=False,
                    )
                    | (
                        ~models.Q(mode_snapshot="fixed_28_days")
                        & models.Q(fixed_anchor_snapshot__isnull=True)
                    )
                ),
                name="subperiod_anchor_snapshot_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="subscriptionperiod",
            index=models.Index(
                fields=["state", "starts_on", "ends_on"],
                name="subperiod_state_dates_ix",
            ),
        ),
        migrations.AddIndex(
            model_name="subscriptionperiod",
            index=models.Index(
                fields=["scheme", "state"],
                name="subperiod_scheme_state_ix",
            ),
        ),
        migrations.AddConstraint(
            model_name="groupplacehold",
            constraint=models.CheckConstraint(
                condition=models.Q(period_until__gte=models.F("period_from")),
                name="grouphold_period_dates_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="groupplacehold",
            constraint=models.UniqueConstraint(
                fields=("student", "group", "period_from"),
                name="grouphold_student_group_period_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="groupplacehold",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending_payment",
                        fee_confirmed_at__isnull=True,
                        fee_confirmed_by__isnull=True,
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                        cancellation_reason="",
                    )
                    | models.Q(
                        status="active",
                        fee_confirmed_at__isnull=False,
                        fee_confirmed_by__isnull=False,
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                        cancellation_reason="",
                    )
                    | models.Q(
                        status="cancelled",
                        cancelled_at__isnull=False,
                        cancellation_reason__gt="",
                    )
                    | models.Q(
                        status="expired",
                        fee_confirmed_at__isnull=False,
                        cancelled_at__isnull=True,
                        cancelled_by__isnull=True,
                        cancellation_reason="",
                    )
                ),
                name="grouphold_status_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="groupplacehold",
            index=models.Index(
                fields=["group", "status", "period_from", "period_until"],
                name="grouphold_group_period_ix",
            ),
        ),
        migrations.AddIndex(
            model_name="groupplacehold",
            index=models.Index(
                fields=["student", "status", "period_from"],
                name="grouphold_student_period_ix",
            ),
        ),
    ]
