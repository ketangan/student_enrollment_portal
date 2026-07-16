from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_schooladminmembership_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="schedule_change_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="submission",
            name="schedule_change_requested_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
