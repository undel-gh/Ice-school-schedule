from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0002_group_capacity_seat_reservation"),
    ]

    operations = [
        migrations.AddField(
            model_name="venue",
            name="floor",
            field=models.CharField(blank=True, default="", max_length=32),
            preserve_default=False,
        ),
    ]
