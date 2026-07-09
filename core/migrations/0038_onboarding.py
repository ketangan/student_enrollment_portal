import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_demoaccesstoken"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="is_demo",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name="demoaccesstoken",
            name="purpose",
            field=models.CharField(
                choices=[("demo", "Demo"), ("onboarding", "Onboarding")],
                default="demo",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="OnboardingChecklistItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item", models.CharField(
                    max_length=50,
                    choices=[
                        ("school_created", "School created"),
                        ("plan_configured", "Plan configured"),
                        ("trial_configured", "Trial configured"),
                        ("admin_invited", "Admin user invited"),
                        ("branding_configured", "Branding configured"),
                        ("programs_configured", "Programs configured"),
                        ("workflows_configured", "Enrollment workflows configured"),
                        ("payment_configured", "Payment workflow configured"),
                        ("email_templates_reviewed", "Email templates reviewed"),
                        ("lead_capture_configured", "Lead capture configured"),
                        ("website_integration_complete", "Website integration complete"),
                        ("test_submission_completed", "Test submission completed"),
                        ("email_delivery_verified", "Email delivery verified"),
                        ("reports_verified", "Reports verified"),
                        ("school_marked_live", "School marked Live"),
                    ],
                )),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("school", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="onboarding_items",
                    to="core.school",
                )),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="onboardingchecklistitem",
            constraint=models.UniqueConstraint(
                fields=["school", "item"],
                name="unique_school_onboarding_item",
            ),
        ),
        migrations.CreateModel(
            name="DemoArchive",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
                ("submissions_json", models.JSONField(default=list)),
                ("leads_json", models.JSONField(default=list)),
                ("config_yaml", models.TextField(blank=True)),
                ("archived_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("school", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="demo_archive",
                    to="core.school",
                )),
            ],
            options={"verbose_name": "Demo Archive"},
        ),
    ]
