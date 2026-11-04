from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0052_square_multiple_accounts")]

    operations = [migrations.AddField(
        model_name="bookingmessage", name="document_snapshot",
        field=models.JSONField(default=dict, blank=True),
    )]
