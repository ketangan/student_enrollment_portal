from django.db import migrations, models
import core.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_submission_form_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                editable=False,
                max_length=16,
                null=True,
                unique=True,
            ),
        ),
    ]
