from django.db import migrations, models


def backfill_revocation_reason(apps, schema_editor):
    AbsenceJustification = apps.get_model(
        "attendance",
        "AbsenceJustification",
    )
    AbsenceJustification.objects.filter(
        status="revoked",
        revocation_reason__isnull=True,
    ).update(revocation_reason="administrative")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="absencejustification",
            name="absence_student_lesson_uq",
        ),
        migrations.RemoveConstraint(
            model_name="absencejustification",
            name="absence_state_metadata_ck",
        ),
        migrations.AddField(
            model_name="absencejustification",
            name="revocation_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("attendance_correction", "Attendance correction"),
                    ("administrative", "Administrative"),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_revocation_reason,
            noop_reverse,
        ),
        migrations.AddConstraint(
            model_name="absencejustification",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["pending", "verified"]),
                fields=("student", "lesson", "type"),
                name="absence_active_student_lesson_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="absencejustification",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending",
                        reviewed_at__isnull=True,
                        revoked_at__isnull=True,
                        revocation_reason__isnull=True,
                    )
                    | models.Q(
                        status="verified",
                        reviewed_at__isnull=False,
                        revoked_at__isnull=True,
                        revocation_reason__isnull=True,
                    )
                    | models.Q(
                        status="rejected",
                        reviewed_at__isnull=False,
                        revoked_at__isnull=True,
                        revocation_reason__isnull=True,
                    )
                    | models.Q(
                        status="revoked",
                        reviewed_at__isnull=False,
                        revoked_at__isnull=False,
                        revocation_reason__isnull=False,
                    )
                ),
                name="absence_state_metadata_ck",
            ),
        ),
    ]
