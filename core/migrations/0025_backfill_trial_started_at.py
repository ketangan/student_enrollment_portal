"""
Backfill trial_started_at for existing trial schools.

For every School with plan='trial' and a null trial_started_at,
set trial_started_at = created_at so the 14-day window starts from
when the school was originally created, not from the deploy date.
Non-trial schools are not touched.
"""
from django.db import migrations
from django.db.models import F


def backfill_trial_started_at(apps, schema_editor):
    School = apps.get_model("core", "School")
    School.objects.filter(plan="trial", trial_started_at__isnull=True).update(
        trial_started_at=F("created_at")
    )


def reverse_backfill(apps, schema_editor):
    # Reversal: clear trial_started_at only on trial schools
    # (safe because the schema migration makes it nullable)
    School = apps.get_model("core", "School")
    School.objects.filter(plan="trial").update(trial_started_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_add_trial_started_at"),
    ]

    operations = [
        migrations.RunPython(backfill_trial_started_at, reverse_code=reverse_backfill),
    ]
