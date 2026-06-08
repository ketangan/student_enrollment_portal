from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_program_management"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolprogram",
            name="is_deleted",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
