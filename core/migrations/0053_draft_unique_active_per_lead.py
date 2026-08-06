from django.db import migrations, models
from django.db.models import Count, Max, Q


def cleanup_duplicate_active_drafts(apps, schema_editor):
    """
    Before adding the unique constraint, remove stale duplicate active drafts.
    For each (school, lead) pair with more than one unsubmitted draft, keep the
    newest (highest id) and delete the rest.  These duplicates pre-date the
    constraint and were never submitted, so deletion is safe.
    """
    DraftSubmission = apps.get_model("core", "DraftSubmission")

    duplicates = (
        DraftSubmission.objects
        .filter(submitted_at__isnull=True, lead__isnull=False)
        .values("school_id", "lead_id")
        .annotate(cnt=Count("id"), newest_id=Max("id"))
        .filter(cnt__gt=1)
    )

    for row in duplicates:
        DraftSubmission.objects.filter(
            school_id=row["school_id"],
            lead_id=row["lead_id"],
            submitted_at__isnull=True,
        ).exclude(id=row["newest_id"]).delete()


class Migration(migrations.Migration):
    """
    Formalises the unique_active_draft_per_lead constraint.

    The index was created manually on the live DB before this migration existed.
    We clean up any pre-existing duplicate active drafts first, then drop the
    raw index (IF EXISTS — safe on fresh test DBs) and re-create it as a formal
    Django UniqueConstraint so the test DB also enforces it.
    """

    dependencies = [
        ("core", "0052_submission_search_text"),
    ]

    operations = [
        # Remove stale duplicate unsubmitted drafts before enforcing uniqueness.
        migrations.RunPython(
            cleanup_duplicate_active_drafts,
            migrations.RunPython.noop,
        ),
        # Drop the manually-created raw index if present (live DB has it; test DBs won't).
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
