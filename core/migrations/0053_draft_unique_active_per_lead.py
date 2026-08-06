from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """
    Formalises the unique_active_draft_per_lead constraint.

    The index was created manually on the live DB before this migration existed,
    so we drop it first with IF EXISTS (safe on fresh test DBs) and re-create it
    as a formal Django UniqueConstraint so that the test DB also enforces it.
    """

    dependencies = [
        ("core", "0052_submission_search_text"),
    ]

    operations = [
        # Drop the manually-created raw index if it exists (live DB has it; test DBs won't).
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS unique_active_draft_per_lead;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="draftsubmission",
            constraint=models.UniqueConstraint(
                fields=["school", "lead"],
                condition=Q(submitted_at__isnull=True),
                name="unique_active_draft_per_lead",
            ),
        ),
    ]
