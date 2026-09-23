from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_auditevent_event_aggregate_idx"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="auditevent",
            name="audit_event_aggregate_idx",
        ),
    ]
