from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_add_school_submission_number"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE core_submission s
                SET school_submission_number = numbered.rn
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY school_id
                            ORDER BY created_at, id
                        ) AS rn
                    FROM core_submission
                ) AS numbered
                WHERE s.id = numbered.id;
            """,
            reverse_sql="""
                UPDATE core_submission SET school_submission_number = NULL;
            """,
        ),
    ]
