from django.db import migrations, models
import scheduling.capabilities


def preserve_existing_access(apps, schema_editor):
    Profile = apps.get_model("scheduling", "SchedulingProfile")
    Profile.objects.using(schema_editor.connection.alias).update(
        capabilities=["workshops", "parties", "reservations", "camps", "lessons"]
    )


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0013_booking_message_outbox")]
    operations = [
        migrations.AddField(
            model_name="schedulingprofile", name="capabilities",
            field=models.JSONField(blank=True, default=list, validators=[scheduling.capabilities.validate_capabilities]),
        ),
        migrations.RunPython(preserve_existing_access, migrations.RunPython.noop),
    ]
