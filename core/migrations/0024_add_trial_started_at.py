from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_backfill_school_submission_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="trial_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
