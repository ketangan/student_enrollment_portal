from django.db import migrations, models
import core.models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_add_ai_summary_to_submission"),
    ]

    operations = [
        migrations.CreateModel(
            name="DraftSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="draft_submissions",
                        to="core.school",
                    ),
                ),
                ("form_key", models.CharField(default="default", max_length=64)),
                ("data", models.JSONField(default=dict)),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        default=core.models._generate_draft_token,
                        max_length=128,
                        unique=True,
                    ),
                ),
                (
                    "token_expires_at",
                    models.DateTimeField(default=core.models._default_token_expires_at),
                ),
                ("email", models.CharField(blank=True, default="", max_length=254)),
                ("last_form_key", models.CharField(blank=True, default="", max_length=64)),
                ("last_email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["token"], name="core_drafts_token_idx"),
                    models.Index(fields=["token_expires_at"], name="core_drafts_expires_idx"),
                ],
            },
        ),
    ]
