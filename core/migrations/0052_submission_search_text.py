from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations, models


def backfill_search_text(apps, schema_editor):
    """Populate search_text for all existing submissions."""
    # Use the live model so _compute_search_text() is available.
    # Acceptable here — search_text is purely derived from already-present fields.
    from core.models import Submission

    subs = list(Submission.objects.select_related("program").only("id", "data", "program_id", "search_text"))
    for sub in subs:
        sub.search_text = sub._compute_search_text()
    if subs:
        Submission.objects.bulk_update(subs, ["search_text"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_school_config_overrides"),
    ]

    operations = [
        # pg_trgm must exist before the GIN index with gin_trgm_ops can be created.
        CreateExtension("pg_trgm"),
        migrations.AddField(
            model_name="submission",
            name="search_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddIndex(
            model_name="submission",
            index=GinIndex(
                fields=["search_text"],
                name="submission_search_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.RunPython(backfill_search_text, migrations.RunPython.noop),
    ]
