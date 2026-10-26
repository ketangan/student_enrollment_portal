from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0041_workshop_deferred_selections")]
    operations = [migrations.AddField(model_name="schedulingprofile",
        name="workshop_notifications_enabled", field=models.BooleanField(default=False))]
