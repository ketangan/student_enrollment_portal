"""
Migration: Program Management — DB-Driven Programs

Adds:
  1. School.program_field_key  (CharField)
  2. SchoolProgram model
  3. Submission.program  (ForeignKey → SchoolProgram, SET_NULL)
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_submission_status_token_public_notes"),
    ]

    operations = [
        # 1. Add program_field_key to School
        migrations.AddField(
            model_name="school",
            name="program_field_key",
            field=models.CharField(blank=True, default="", max_length=120),
        ),

        # 2. Create SchoolProgram model
        migrations.CreateModel(
            name="SchoolProgram",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="programs",
                        to="core.school",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("code", models.CharField(db_index=True, max_length=120)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("capacity", models.PositiveIntegerField(blank=True, null=True)),
                ("auto_enroll", models.BooleanField(default=False)),
                ("waitlist_enabled", models.BooleanField(default=False)),
                ("form_keys", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["display_order", "name"],
                "unique_together": {("school", "code")},
            },
        ),

        # 3. Add Submission.program FK
        migrations.AddField(
            model_name="submission",
            name="program",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submissions",
                to="core.schoolprogram",
            ),
        ),
    ]
