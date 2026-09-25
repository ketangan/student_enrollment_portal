"""
Data migration: copy SBMC's hardcoded YAML notification emails into the new
School model fields so the YAML can be safely cleared without losing the
existing email routing.
"""
from django.db import migrations


def seed_sbmc_emails(apps, schema_editor):
    School = apps.get_model("core", "School")
    try:
        sbmc = School.objects.get(slug="south-bay-music")
        sbmc.notification_to_emails = "info@sbmusicconservatory.com"
        sbmc.leads_notify_to_emails = "info@sbmusicconservatory.com"
        sbmc.save(update_fields=["notification_to_emails", "leads_notify_to_emails"])
    except School.DoesNotExist:
        pass


def reverse_sbmc_emails(apps, schema_editor):
    School = apps.get_model("core", "School")
    try:
        sbmc = School.objects.get(slug="south-bay-music")
        sbmc.notification_to_emails = ""
        sbmc.leads_notify_to_emails = ""
        sbmc.save(update_fields=["notification_to_emails", "leads_notify_to_emails"])
    except School.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_school_notification_email_fields"),
    ]

    operations = [
        migrations.RunPython(seed_sbmc_emails, reverse_code=reverse_sbmc_emails),
    ]
