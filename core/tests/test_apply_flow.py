import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Submission, SubmissionFile
from core.services.config_loader import load_school_config


def _make_fake_upload(filename: str = "odometer.jpg") -> SimpleUploadedFile:
    # small but non-empty so size formatting / storage behaves
    content = b"\xff\xd8\xff" + (b"a" * 2048) + b"\xff\xd9"
    return SimpleUploadedFile(filename, content, content_type="image/jpeg")


def _attach_files_for_form(form: dict) -> dict:
    """
    Build a dict of {field_key: SimpleUploadedFile(...)} for any YAML fields of type=file.
    We attach for ALL file fields (required or optional) to exercise the path + saving behavior.
    """
    files = {}
    for section in form.get("sections", []):
        for field in section.get("fields", []):
            ftype = (field.get("type") or "").strip().lower()
            if ftype == "file":
                key = field["key"]
                files[key] = _make_fake_upload()
    return files


def _build_valid_post_data(form: dict) -> dict:
    """
    Minimal valid payload generator for YAML forms.
    We skip file types here (they go via FILES), and fill required fields with safe defaults.
    """
    data = {}

    for section in form.get("sections", []):
        for field in section.get("fields", []):
            key = field["key"]
            ftype = (field.get("type") or "text").strip().lower()
            required = bool(field.get("required", False))

            if ftype == "file":
                continue

            if not required:
                continue

            if ftype in ("text", "textarea", "tel"):
                data[key] = "Test"
            elif ftype == "email":
                data[key] = "test@example.com"
            elif ftype == "date":
                data[key] = "2010-01-01"
            elif ftype == "number":
                data[key] = "10"
            elif ftype == "checkbox":
                data[key] = "on"
            elif ftype == "select":
                opts = field.get("options") or []
                if opts:
                    data[key] = opts[0].get("value") or opts[0].get("label") or "option"
                else:
                    data[key] = "option"
            elif ftype == "multiselect":
                # Django test client supports list for multiselect
                opts = field.get("options") or []
                v = (opts[0].get("value") if opts else "mon")
                data[key] = [v]
            else:
                # fallback
                data[key] = "Test"

    return data


@pytest.mark.django_db
def test_apply_flow_creates_submission_and_redirects(client):
    # This slug must exist in configs/schools in your repo
    slug = "enrollment-request-demo"

    cfg = load_school_config(slug)
    assert cfg is not None, f"Missing config for {slug} (configs/schools/{slug}.yaml)"

    url = reverse("apply", kwargs={"school_slug": slug})

    post_data = _build_valid_post_data(cfg.form)
    file_data = _attach_files_for_form(cfg.form)

    # Put uploaded file objects directly into post_data so Django builds request.FILES
    post_data.update(file_data)

    resp = client.post(url, data=post_data, follow=False)
    assert resp.status_code in (302, 303)

    submission = Submission.objects.order_by("-id").first()
    assert submission is not None
    assert submission.school.slug == slug

    # If the YAML had any file fields, we should have SubmissionFile rows
    expected_files = len(file_data)
    actual_files = SubmissionFile.objects.filter(submission=submission).count()
    assert actual_files == expected_files
    