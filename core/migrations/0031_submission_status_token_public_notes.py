import secrets

from django.db import migrations, models
from core.models import generate_submission_status_token


def _backfill_status_tokens(apps, schema_editor):
    Submission = apps.get_model("core", "Submission")
    for sub in Submission.objects.filter(status_token__exact=""):
        sub.status_token = secrets.token_urlsafe(32)
        sub.save(update_fields=["status_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_application_fee_fields"),
    ]

    operations = [
        # Step 1: add both fields — status_token starts nullable/blank so the
        # SQL ALTER TABLE doesn't try to assign one constant default to all rows
        # (which would immediately violate the unique constraint we add next).
        migrations.AddField(
            model_name="submission",
            name="status_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="submission",
            name="public_notes",
            field=models.TextField(blank=True, default=""),
        ),
        # Step 2: backfill unique tokens for every existing row
        migrations.RunPython(_backfill_status_tokens, migrations.RunPython.noop),
        # Step 3: enforce uniqueness now that every row has a distinct value
        migrations.AlterField(
            model_name="submission",
            name="status_token",
            field=models.CharField(default=generate_submission_status_token, max_length=64, unique=True),
        ),
    ]
