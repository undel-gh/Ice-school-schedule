from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(
                fields=["event_type", "aggregate_type", "aggregate_id"],
                name="audit_event_aggregate_idx",
            ),
        ),
    ]
