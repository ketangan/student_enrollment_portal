from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_add_stripe_fields_to_school"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="school",
            name="stripe_cancel_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="school",
            name="stripe_cancel_at_period_end",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="school",
            name="stripe_current_period_end",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
