from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_schoolsession_code_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="lead_webhook_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="lead",
            name="data",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
