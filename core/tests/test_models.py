import pytest

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.tests.factories import SchoolFactory, SubmissionFactory
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import School, SchoolFeatures, SubmissionFile



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
    assert school.plan == School.PLAN_TRIAL


@pytest.mark.django_db
def test_school_feature_flags_defaults_to_empty_dict_or_seeded():
    """Creating a school with no explicit flags should seed plan defaults via save()."""
    school = School.objects.create(slug="plan-seed-test", plan="trial")
    # save() override seeds defaults when feature_flags is empty on creation
    assert isinstance(school.feature_flags, dict)
    assert "reports_enabled" in school.feature_flags


@pytest.mark.django_db
def test_school_save_does_not_overwrite_existing_flags():
    """If feature_flags is explicitly set on creation, save() should not overwrite."""
    flags = {"reports_enabled": True, "custom": True}
    school = School.objects.create(slug="keep-flags-test", plan="trial", feature_flags=flags)
    assert school.feature_flags == flags


@pytest.mark.django_db
def test_school_save_does_not_re_seed_on_update():
    """Updating an existing school should not re-seed feature_flags."""
    school = School.objects.create(slug="update-test", plan="trial")
    # manually clear flags after creation
    School.objects.filter(pk=school.pk).update(feature_flags={})
    school.refresh_from_db()
    assert school.feature_flags == {}

    # update display_name (not adding) -> save should not re-seed
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
    choice_values = {v for v, _ in School.PLAN_CHOICES}
    assert School.PLAN_TRIAL in choice_values
    assert School.PLAN_STARTER in choice_values
    assert School.PLAN_PRO in choice_values
    assert School.PLAN_GROWTH in choice_values
