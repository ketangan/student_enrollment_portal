from __future__ import annotations

import base64
import secrets

from django.db import IntegrityError, migrations, models
from django.db.models import Q
import core.models


def _generate_public_id() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")


def backfill_public_id(apps, schema_editor) -> None:
    Submission = apps.get_model("core", "Submission")

    # Only rows missing a value (null or empty string)
    qs = Submission.objects.filter(Q(public_id__isnull=True) | Q(public_id=""))

    batch_size = 1000
    ids = list(qs.values_list("id", flat=True).order_by("id"))

    for start in range(0, len(ids), batch_size):
        chunk_ids = ids[start : start + batch_size]
        # Update one row at a time to avoid backend/ORM quirks with historical models.
        for submission_id in chunk_ids:
            for _ in range(10):
                pid = _generate_public_id()
                try:
                    updated = Submission.objects.filter(
                        id=submission_id
                    ).filter(
                        Q(public_id__isnull=True) | Q(public_id="")
                    ).update(public_id=pid)

                    if updated == 1:
                        break
                except IntegrityError:
                    continue


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_submission_public_id"),
    ]

    operations = [
        migrations.RunPython(backfill_public_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="submission",
            name="public_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default=core.models.generate_public_id,
                editable=False,
                max_length=16,
                null=False,
                unique=True,
            ),
        ),
    ]
