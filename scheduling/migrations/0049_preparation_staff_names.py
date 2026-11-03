from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0048_bookingmessage_created_by_bookingmessage_is_html")]

    operations = [
        migrations.AddField(model_name="session", name="staff_names",
            field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="reservation", name="staff_names",
            field=models.CharField(blank=True, max_length=200)),
    ]
