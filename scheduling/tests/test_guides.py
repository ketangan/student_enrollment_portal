from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from pypdf import PdfWriter

from scheduling.guides import GuideForm, replace
from scheduling.models import DesignGuide, SchedulingMembership
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


def pdf(title="Guide"):
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=480, height=572)
    writer.add_metadata({"/Title": title})
    writer.write(stream)
    return SimpleUploadedFile("guide.pdf", stream.getvalue(), content_type="application/pdf")


def upload(stock, version=0, title="Guide"):
    form = GuideForm({"current_version": version}, {"file": pdf(title)})
    assert form.is_valid(), form.errors
    return replace(stock.user, stock.school.slug, form)


@pytest.fixture(autouse=True)
def storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True


def test_upload_replacement_and_stable_link(client, stock):
    first = upload(stock)
    second = upload(stock, first.pk, "New")
    path = reverse("scheduling:design_guide", kwargs={"school_slug": stock.school.slug})
    response = client.get(path)
    assert response.status_code == 200
    assert b"New" in b"".join(response.streaming_content)
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    assert "no-store" in response["Cache-Control"]
    assert DesignGuide.objects.count() == 2
    assert upload(stock, second.pk, "New").pk == second.pk
    with pytest.raises(ValidationError):
        upload(stock, first.pk, "Stale")
    assert first.file.storage.exists(first.file.name)


def test_permissions_and_versions(client, stock):
    guide = upload(stock)
    kwargs = {"school_slug": stock.school.slug}
    page = reverse("scheduling:guest_communication", kwargs=kwargs)
    version = reverse("scheduling:design_guide_version", kwargs={**kwargs, "pk": guide.pk})
    assert client.get(page).status_code == 302
    assert client.get(version).status_code == 404
    client.force_login(stock.user)
    assert client.get(page).status_code == 200
    assert client.get(version).status_code == 200
    for role in ("scheduler", "viewer"):
        SchedulingMembership.objects.filter(user=stock.user).update(role=role)
        assert client.get(page).status_code == 404
        assert client.post(page, {"current_version": guide.pk, "file": pdf()}).status_code == 404
        assert client.get(version).status_code == 404


@pytest.mark.parametrize("name,data", [("guide.txt", b"text"), ("guide.pdf", b"%PDF-not-a-pdf"),
    ("guide.pdf", b"x" * (5 * 1024 * 1024 + 1))])
def test_rejects_invalid_uploads(name, data):
    form = GuideForm({"current_version": 0}, {"file": SimpleUploadedFile(name, data)})
    assert not form.is_valid()


def test_rejects_active_or_encrypted_pdf():
    for encrypted in (True, False):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        if encrypted:
            writer.encrypt("password")
        else:
            writer.add_js("app.alert('test')")
        output = BytesIO(); writer.write(output)
        form = GuideForm({"current_version": 0}, {"file": SimpleUploadedFile("guide.pdf", output.getvalue())})
        assert not form.is_valid()


def test_production_requires_verified_storage(settings):
    settings.DEBUG = False
    settings.BOOKINGS_GUIDE_STORAGE_READY = False
    assert not GuideForm({"current_version": 0}, {"file": pdf()}).is_valid()


def test_owner_upload_form_and_stale_post(client, stock):
    client.force_login(stock.user)
    path = reverse("scheduling:guest_communication", kwargs={"school_slug": stock.school.slug})
    assert client.post(path, {"current_version": 0, "file": pdf()}).status_code == 302
    assert DesignGuide.objects.count() == 1
    assert client.post(path, {"current_version": 0, "file": pdf("New")}).status_code == 400
    assert DesignGuide.objects.count() == 1


def test_storage_is_cleaned_up_if_database_write_rolls_back(stock, monkeypatch, settings):
    from pathlib import Path
    def fail(*args):
        raise RuntimeError("Audit unavailable")
    monkeypatch.setattr("scheduling.lessons.audit", fail)
    with pytest.raises(RuntimeError):
        upload(stock)
    assert not DesignGuide.objects.exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.pdf"))


def test_guide_environment_boundary(client, stock, settings):
    upload(stock)
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com"]
    path = reverse("scheduling:design_guide", kwargs={"school_slug": stock.school.slug})
    response = client.get(path, HTTP_HOST="demo.example.com")
    assert response.status_code == 302
    assert response["Location"] == "https://app.example.com" + path
    assert client.get(path, HTTP_HOST="app.example.com").status_code == 200
    stock.profile.enabled = False; stock.profile.save()
    assert client.get(path, HTTP_HOST="app.example.com").status_code == 404


def test_owner_notification_toggle_and_missing_download(client, stock):
    client.force_login(stock.user)
    page = reverse("scheduling:guest_communication", kwargs={"school_slug": stock.school.slug})
    download = reverse("scheduling:design_guide", kwargs={"school_slug": stock.school.slug})
    assert client.get(download).status_code == 404
    for enabled in (True, False):
        data = {"action": "notifications"}
        if enabled:
            data["party_notifications_enabled"] = "on"
        assert client.post(page, data).status_code == 302
        stock.profile.refresh_from_db()
        assert stock.profile.party_notifications_enabled is enabled
    guide = upload(stock)
    guide.file.storage.delete(guide.file.name)
    assert client.get(download).status_code == 404


def test_shared_guide_and_enrollment_navigation_tags(stock):
    from types import SimpleNamespace
    from scheduling.navigation import enrollment_url
    from scheduling.templatetags.booking_navigation import booking_design_guide, booking_enrollment_url
    assert booking_design_guide(None) == ""
    assert booking_design_guide(stock.school) == ""
    upload(stock)
    assert booking_design_guide(stock.school) == reverse("scheduling:design_guide", kwargs={"school_slug": stock.school.slug})
    context = {"request": SimpleNamespace(user=stock.user)}
    assert booking_enrollment_url(context, stock.profile) == enrollment_url(stock.user, stock.profile)
    stock.profile.enabled = False
    stock.profile.save()
    assert booking_design_guide(stock.school) == ""
