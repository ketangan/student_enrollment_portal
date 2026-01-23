import pytest

from core.tests.factories import SchoolFactory, SubmissionFactory
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import SubmissionFile



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
    