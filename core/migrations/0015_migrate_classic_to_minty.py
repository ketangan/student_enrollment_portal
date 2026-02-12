"""Data migration: rename theme key 'classic' → 'minty' in AdminPreference rows."""

from django.db import migrations


def classic_to_minty(apps, schema_editor):
    AdminPreference = apps.get_model("core", "AdminPreference")
    AdminPreference.objects.filter(theme="classic").update(theme="minty")


def minty_to_classic(apps, schema_editor):
    AdminPreference = apps.get_model("core", "AdminPreference")
    AdminPreference.objects.filter(theme="minty").update(theme="classic")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_update_theme_choices_classic_to_minty"),
    ]

    operations = [
        migrations.RunPython(classic_to_minty, reverse_code=minty_to_classic),
    ]
