from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0059_demo_notifications_since")]

    operations = [migrations.AddField(
        model_name="session", name="canceled_at",
        field=models.DateTimeField(blank=True, null=True),
    )]
