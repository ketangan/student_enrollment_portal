from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0040_location_inventory_buffer_days_incomingdelivery")]
    operations = [
        migrations.AddField(model_name="offering", name="allow_deferred_selections", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="requestparticipant", name="selections_deferred", field=models.BooleanField(default=False, editable=False)),
    ]
