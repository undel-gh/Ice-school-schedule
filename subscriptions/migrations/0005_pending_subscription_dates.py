from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0004_subscription_periods_place_holds"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="subscription",
            name="subscription_until_gte_from",
        ),
        migrations.AlterField(
            model_name="subscription",
            name="valid_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="subscription",
            name="valid_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="subscription",
            constraint=models.CheckConstraint(
                condition=(
                    Q(valid_from__isnull=True, valid_until__isnull=True)
                    | Q(
                        valid_from__isnull=False,
                        valid_until__isnull=False,
                        valid_until__gte=F("valid_from"),
                    )
                ),
                name="subscription_dates_ck",
            ),
        ),
    ]
