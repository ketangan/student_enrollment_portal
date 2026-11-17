from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0072_schoolemailtemplate_booking_action_enabled_and_more')]
    operations = [
        migrations.AddField(model_name='schoolemailtemplate', name='booking_links',
            field=models.JSONField(null=True, blank=True, default=None)),
        migrations.AddField(model_name='schoolemailtemplate', name='booking_details_in_body',
            field=models.BooleanField(default=False)),
    ]
