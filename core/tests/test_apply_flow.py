import pytest
from pathlib import Path

from django.urls import reverse

from core.services.config_loader import load_school_config
from core.models import Submission


SCHOOL_SLUGS = [
    "dancemaker-studio",
    "kimberlas-classical-ballet",
    "torrance-sister-city-association",
]


def make_valid_value_for_field(field: dict):
    ftype = field.get("type")
    if ftype in ("text", "textarea", "name"):
        return "Test"
    if ftype == "email":
        return "test@example.com"
    if ftype == "date":
        return "2020-01-01"
    if ftype == "number":
        return "1"
    if ftype == "checkbox":
        return "on"
    if ftype == "select":
        options = field.get("options") or []
        if options:
            return str(options[0].get("value"))
        return ""
    if ftype == "multiselect":
        options = field.get("options") or []
        if options:
            return [str(options[0].get("value"))]
        return []

    # default fallback
    return "Test"


def build_valid_post(form: dict):
    data = {}
    for section in form.get("sections", []):
        for field in section.get("fields", []):
            if field.get("required"):
                val = make_valid_value_for_field(field)
                data[field["key"]] = val
    return data


@pytest.mark.django_db
@pytest.mark.parametrize("slug", SCHOOL_SLUGS)
def test_get_apply_page_returns_200(client, slug):
    url = reverse("apply", kwargs={"school_slug": slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("slug", SCHOOL_SLUGS)
def test_post_valid_creates_submission_and_redirects(client, slug):
    config = load_school_config(slug)
    assert config is not None

    form = config.form
    post = build_valid_post(form)

    url = reverse("apply", kwargs={"school_slug": slug})
    before = Submission.objects.count()
    resp = client.post(url, data=post)

    # should redirect to success
    assert resp.status_code in (302, 301)
    assert reverse("apply_success", kwargs={"school_slug": slug}) in resp["Location"]
    assert Submission.objects.count() == before + 1


@pytest.mark.django_db
@pytest.mark.parametrize("slug", SCHOOL_SLUGS)
def test_post_invalid_does_not_create_submission_and_renders_errors(client, slug):
    config = load_school_config(slug)
    assert config is not None
    form = config.form

    # Build an invalid post: omit all required fields
    post = {}

    url = reverse("apply", kwargs={"school_slug": slug})
    before = Submission.objects.count()
    resp = client.post(url, data=post)

    # Should not redirect; render form with errors
    assert resp.status_code == 200
    assert Submission.objects.count() == before

    # errors should be present in context (mapping of field_key -> message)
    ctx = getattr(resp, "context", None)
    if ctx:
        errors = ctx.get("errors")
        # If the site had no required fields, errors may be empty; just check type
        assert isinstance(errors, dict)
