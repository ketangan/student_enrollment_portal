from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_draftsubmission_add_lead"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="internal_notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
