from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_school_smtp_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="form_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=100),
        ),
    ]
