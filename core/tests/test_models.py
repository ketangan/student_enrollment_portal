import pytest

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.tests.factories import SchoolFactory, SubmissionFactory
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import School, SchoolFeatures, SubmissionFile
from core.services.feature_flags import (
    PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH, PLAN_CHOICES,
)



@pytest.mark.django_db
def test_student_display_name_prefers_first_last_then_applicant():
    # first/last present
    sub = SubmissionFactory(data={"first_name": "Jane", "last_name": "Doe"})
    assert sub.student_display_name() == "Jane Doe"

    # applicant_name fallback
    sub2 = SubmissionFactory(data={"applicant_name": "Solo Applicant"})
    assert sub2.student_display_name() == "Solo Applicant"


@pytest.mark.django_db
def test_program_display_name_class_name_label_resolution():
    # with label_map resolving
    school = SchoolFactory()
    sub = SubmissionFactory(school=school, data={"class_name": "cls-101"})
    label_map = {"class_name": {"cls-101": "Intro Class"}}
    assert sub.program_display_name(label_map=label_map) == "Intro Class"

    # without label_map falls back to raw
    assert sub.program_display_name(label_map={}) == "cls-101"


@pytest.mark.django_db
def test_program_display_name_dancemaker_combines_style_and_level():
    school = SchoolFactory()
    data = {"dance_style": "ballet", "skill_level": "intermediate"}
    sub = SubmissionFactory(school=school, data=data)

    label_map = {"dance_style": {"ballet": "Ballet"}, "skill_level": {"intermediate": "Intermediate"}}
    assert sub.program_display_name(label_map=label_map) == "Ballet (Intermediate)"

    # partial mapping: missing level label falls back to raw
    label_map2 = {"dance_style": {"ballet": "Ballet"}, "skill_level": {}}
    assert sub.program_display_name(label_map=label_map2) == "Ballet (intermediate)"

    # only dance_style present
    sub2 = SubmissionFactory(school=school, data={"dance_style": "hiphop"})
    assert sub2.program_display_name(label_map={}) == "hiphop"


@pytest.mark.django_db
def test_program_display_name_tsca_and_empty_cases():
    # TSCA school slug should return Student Exchange
    tsca = SchoolFactory(slug="torrance-sister-city-association")
    sub = SubmissionFactory(school=tsca, data={})
    assert sub.program_display_name() == "Student Exchange"

    # non-TSCA empty returns empty string
    other = SchoolFactory(slug="some-other-school")
    sub2 = SubmissionFactory(school=other, data={})
    assert sub2.program_display_name() == ""

@pytest.mark.django_db
def test_submissionfile_str_includes_school_slug_submission_and_field_key():
    sub = SubmissionFactory()
    f = SubmissionFile.objects.create(
        submission=sub,
        field_key="id_document",
        file=SimpleUploadedFile("odometer.jpg", b"abc", content_type="image/jpeg"),
    )

    s = str(f)
    assert sub.school.slug in s
    assert str(sub.id) in s
    assert "id_document" in s


@pytest.mark.django_db
def test_submissionfile_upload_path_contains_school_slug_and_submission_id():
    sub = SubmissionFactory()
    f = SubmissionFile.objects.create(
        submission=sub,
        field_key="id_document",
        file=SimpleUploadedFile("odometer.jpg", b"abc", content_type="image/jpeg"),
    )

    # stored path includes uploads/<school_slug>/<submission_id>/
    assert f.file.name.startswith(f"uploads/{sub.school.slug}/{sub.id}/")


@pytest.mark.django_db
def test_submission_public_id_is_unique_and_url_safe():
    subs = SubmissionFactory.create_batch(25)
    public_ids = [s.public_id for s in subs]

    assert all(isinstance(pid, str) and pid for pid in public_ids)
    assert len(set(public_ids)) == len(public_ids)

    # urlsafe base64 chars without padding
    for pid in public_ids:
        assert "=" not in pid
        assert len(pid) <= 16
        assert pid.replace("-", "").replace("_", "").isalnum()


@pytest.mark.django_db
def test_submission_status_defaults_to_new():
    sub = SubmissionFactory()
    assert sub.status == "New"


@pytest.mark.django_db(transaction=True)
def test_migration_backfills_public_id_for_existing_rows():
    executor = MigrationExecutor(connection)

    # Step 1: migrate to state where public_id exists but is nullable
    executor.migrate([("core", "0006_submission_public_id")])
    state = executor.loader.project_state([("core", "0006_submission_public_id")])
    School = state.apps.get_model("core", "School")
    Submission = state.apps.get_model("core", "Submission")

    school = School.objects.create(slug="migrate-test", display_name="Migrate Test")
    sub = Submission.objects.create(school=school, form_key="default", data={}, public_id=None)
    assert sub.public_id is None

    # Step 2: migrate forward to backfill+non-null
    # Re-instantiate to refresh applied migration state
    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0007_backfill_submission_public_id")])

    state2 = executor.loader.project_state([("core", "0007_backfill_submission_public_id")])
    Submission2 = state2.apps.get_model("core", "Submission")
    refreshed = Submission2.objects.get(id=sub.id)
    assert refreshed.public_id
    assert "=" not in refreshed.public_id
    assert len(refreshed.public_id) <= 16

    # Restore to latest for other tests
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


# ---------------------------------------------------------------------------
# School plan / feature flags / SchoolFeatures
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_plan_defaults_to_trial():
    school = SchoolFactory()
    assert school.plan == PLAN_TRIAL


@pytest.mark.django_db
def test_school_feature_flags_defaults_to_empty_dict():
    """Creating a school stores no flags — defaults come from the plan tier."""
    school = School.objects.create(slug="plan-seed-test", plan="trial")
    assert school.feature_flags == {}


@pytest.mark.django_db
def test_school_save_preserves_explicit_flags():
    """Explicit feature_flags are persisted as-is (no seeding on save)."""
    flags = {"reports_enabled": True, "custom": True}
    school = School.objects.create(slug="keep-flags-test", plan="trial", feature_flags=flags)
    assert school.feature_flags == flags


@pytest.mark.django_db
def test_school_save_does_not_inject_flags_on_update():
    """Updating a school should never inject plan-default flags."""
    school = School.objects.create(slug="update-test", plan="trial")
    school.display_name = "Updated"
    school.save()
    school.refresh_from_db()
    assert school.feature_flags == {}


@pytest.mark.django_db
def test_school_features_property_returns_school_features_dataclass():
    school = SchoolFactory(plan="starter")
    features = school.features
    assert isinstance(features, SchoolFeatures)
    assert features.school is school


@pytest.mark.django_db
def test_school_features_reports_enabled_follows_plan():
    trial_school = SchoolFactory(plan="trial")
    assert trial_school.features.reports_enabled is False

    starter_school = SchoolFactory(plan="starter")
    assert starter_school.features.reports_enabled is True


@pytest.mark.django_db
def test_school_features_reports_enabled_respects_override():
    school = SchoolFactory(plan="trial", feature_flags={"reports_enabled": True})
    assert school.features.reports_enabled is True

    school2 = SchoolFactory(plan="pro", feature_flags={"reports_enabled": False})
    assert school2.features.reports_enabled is False


@pytest.mark.django_db
def test_school_features_status_enabled_defaults_true():
    school = SchoolFactory(plan="trial")
    assert school.features.status_enabled is True


@pytest.mark.django_db
def test_school_features_status_enabled_respects_override():
    school = SchoolFactory(plan="starter", feature_flags={"status_enabled": False})
    assert school.features.status_enabled is False


@pytest.mark.django_db
def test_school_features_csv_export_enabled_defaults_true():
    school = SchoolFactory(plan="trial")
    assert school.features.csv_export_enabled is True


@pytest.mark.django_db
def test_school_features_csv_export_enabled_respects_override():
    school = SchoolFactory(plan="pro", feature_flags={"csv_export_enabled": False})
    assert school.features.csv_export_enabled is False


@pytest.mark.django_db
def test_school_features_audit_log_enabled_defaults_true():
    school = SchoolFactory(plan="growth")
    assert school.features.audit_log_enabled is True


@pytest.mark.django_db
def test_school_features_audit_log_enabled_respects_override():
    school = SchoolFactory(plan="starter", feature_flags={"audit_log_enabled": False})
    assert school.features.audit_log_enabled is False


@pytest.mark.django_db
def test_school_features_flags_helper_returns_merged_dict():
    school = SchoolFactory(plan="trial", feature_flags={"reports_enabled": True})
    flags = school.features._flags()
    assert flags["reports_enabled"] is True
    assert flags["status_enabled"] is True


@pytest.mark.django_db
def test_school_plan_choices():
    """All PLAN_* constants are represented in PLAN_CHOICES."""
    choice_values = {v for v, _ in PLAN_CHOICES}
    assert PLAN_TRIAL in choice_values
    assert PLAN_STARTER in choice_values
    assert PLAN_PRO in choice_values
    assert PLAN_GROWTH in choice_values


# ---------------------------------------------------------------------------
# SchoolFeatures — new flag properties (email, uploads, branding, multi, custom)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_features_email_notifications_follows_plan():
    assert SchoolFactory(plan="trial").features.email_notifications_enabled is False
    assert SchoolFactory(plan="starter").features.email_notifications_enabled is True
    assert SchoolFactory(plan="pro").features.email_notifications_enabled is True


@pytest.mark.django_db
def test_school_features_email_notifications_respects_override():
    school = SchoolFactory(plan="trial", feature_flags={"email_notifications_enabled": True})
    assert school.features.email_notifications_enabled is True


@pytest.mark.django_db
def test_school_features_file_uploads_follows_plan():
    assert SchoolFactory(plan="trial").features.file_uploads_enabled is False
    assert SchoolFactory(plan="starter").features.file_uploads_enabled is True
    assert SchoolFactory(plan="pro").features.file_uploads_enabled is True


@pytest.mark.django_db
def test_school_features_file_uploads_respects_override():
    school = SchoolFactory(plan="starter", feature_flags={"file_uploads_enabled": False})
    assert school.features.file_uploads_enabled is False


@pytest.mark.django_db
def test_school_features_custom_branding_follows_plan():
    assert SchoolFactory(plan="trial").features.custom_branding_enabled is False
    assert SchoolFactory(plan="starter").features.custom_branding_enabled is False
    assert SchoolFactory(plan="pro").features.custom_branding_enabled is True


@pytest.mark.django_db
def test_school_features_custom_branding_respects_override():
    school = SchoolFactory(plan="trial", feature_flags={"custom_branding_enabled": True})
    assert school.features.custom_branding_enabled is True


@pytest.mark.django_db
def test_school_features_multi_form_follows_plan():
    assert SchoolFactory(plan="trial").features.multi_form_enabled is False
    assert SchoolFactory(plan="starter").features.multi_form_enabled is False
    assert SchoolFactory(plan="pro").features.multi_form_enabled is True


@pytest.mark.django_db
def test_school_features_multi_form_respects_override():
    school = SchoolFactory(plan="starter", feature_flags={"multi_form_enabled": True})
    assert school.features.multi_form_enabled is True


@pytest.mark.django_db
def test_school_features_custom_statuses_follows_plan():
    assert SchoolFactory(plan="trial").features.custom_statuses_enabled is False
    assert SchoolFactory(plan="starter").features.custom_statuses_enabled is False
    assert SchoolFactory(plan="pro").features.custom_statuses_enabled is True


@pytest.mark.django_db
def test_school_features_custom_statuses_respects_override():
    school = SchoolFactory(plan="pro", feature_flags={"custom_statuses_enabled": False})
    assert school.features.custom_statuses_enabled is False


@pytest.mark.django_db
def test_school_features_caching_is_effective():
    """Repeated access to school.features returns the same cached instance."""
    school = SchoolFactory(plan="trial")
    f1 = school.features
    f2 = school.features
    assert f1 is f2
    assert f1.reports_enabled is False


@pytest.mark.django_db
def test_school_features_cache_invalidated_after_refresh():
    """After refresh_from_db, a new SchoolFeatures is created with fresh data."""
    school = SchoolFactory(plan="trial")
    assert school.features.reports_enabled is False

    # Simulate admin upgrading the plan via DB
    School.objects.filter(pk=school.pk).update(plan="pro")
    school.refresh_from_db()
    # refresh_from_db clears Python-level attrs, so _features_cache is gone
    assert school.features.reports_enabled is True
