from django.db import migrations, models


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
