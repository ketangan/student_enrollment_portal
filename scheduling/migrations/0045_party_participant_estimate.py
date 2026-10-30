from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0044_party_inquiry_notes")]
    operations = [migrations.AddField(model_name="partyrequest", name="participant_estimate",
                                    field=models.CharField(blank=True, max_length=32))]
