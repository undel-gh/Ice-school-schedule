from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="makeupentitlement",
            name="makeup_student_lesson_uq",
        ),
        migrations.AddConstraint(
            model_name="makeupentitlement",
            constraint=models.UniqueConstraint(
                condition=models.Q(cancelled_at__isnull=True),
                fields=("student", "source_lesson", "reason"),
                name="makeup_active_student_lesson_uq",
            ),
        ),
    ]
