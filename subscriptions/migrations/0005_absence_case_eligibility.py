from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0004_absence_compensation_case"),
    ]

    operations = [
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
                    )
                ),
                name="absence_case_period_ck",
            ),
        ),
    ]
