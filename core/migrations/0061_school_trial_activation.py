from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0060_seed_sbmc_notification_emails")]
    operations = [
        migrations.AddField(
            model_name="school", name="trial_duration_days",
            field=models.PositiveSmallIntegerField(
                default=30, db_default=30, editable=False,
                validators=[MinValueValidator(1), MaxValueValidator(365)],
            ),
        ),
        migrations.AddField(
            model_name="school", name="trial_requires_activation",
            field=models.BooleanField(default=False, db_default=False, editable=False),
        ),
    ]
