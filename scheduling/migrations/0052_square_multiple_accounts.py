from django.db import migrations, models


def mapping_environments(apps, schema_editor):
    Mapping = apps.get_model("scheduling", "SquareLocationMapping")
    for environment in ("test", "live"):
        Mapping.objects.using(schema_editor.connection.alias).filter(
            connection__environment=environment).update(environment=environment)


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0051_workshop_price")]

    operations = [
        migrations.AddField("squareconnection", "label", models.CharField(max_length=80, blank=True)),
        migrations.AddField("squarelocationmapping", "environment", models.CharField(
            max_length=8, choices=[("test", "Sandbox"), ("live", "Production")], default="test")),
        migrations.RunPython(mapping_environments, migrations.RunPython.noop),
        migrations.RemoveConstraint("squareconnection", "square_profile_environment_unique"),
        migrations.AddConstraint("squareconnection", models.UniqueConstraint(
            fields=["profile", "environment"], condition=models.Q(merchant_id=""), name="square_pending_account_unique")),
        migrations.RemoveConstraint("squarelocationmapping", "square_location_mapping_unique"),
        migrations.AddConstraint("squarelocationmapping", models.UniqueConstraint(
            fields=["location", "environment"], name="square_location_environment_unique")),
        migrations.AddConstraint("squarelocationmapping", models.CheckConstraint(
            condition=models.Q(environment__in=["test", "live"]), name="square_mapping_environment_valid")),
    ]
