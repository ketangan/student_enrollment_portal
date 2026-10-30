from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0046_location_default_tax_rate")]
    operations = [migrations.AddField(
        model_name="workshoprequest", name="purchaser_phone",
        field=models.CharField(max_length=40, blank=True, default=""))]
