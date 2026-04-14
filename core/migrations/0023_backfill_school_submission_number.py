from collections import defaultdict

from django.db import migrations


def backfill_school_submission_number(apps, schema_editor):
    Submission = apps.get_model("core", "Submission")
    submissions = list(Submission.objects.order_by("school_id", "created_at", "id"))
    counters = defaultdict(int)
    for sub in submissions:
        counters[sub.school_id] += 1
        sub.school_submission_number = counters[sub.school_id]
    Submission.objects.bulk_update(submissions, ["school_submission_number"], batch_size=500)


def reverse_backfill(apps, schema_editor):
    Submission = apps.get_model("core", "Submission")
    Submission.objects.update(school_submission_number=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_add_school_submission_number"),
    ]

    operations = [
        migrations.RunPython(backfill_school_submission_number, reverse_code=reverse_backfill),
    ]
