import pytest
from django.urls import reverse

from core.models import AdminAuditLog, School, SchoolEmailTemplate
from scheduling.email_settings import SchoolEmailSettingsForm
from scheduling.models import SchedulingMembership
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


def page(stock):
    return reverse("scheduling:email_settings", kwargs={"school_slug": stock.school.slug})


def data(**changes):
    return {"action": "save", "smtp_host": "smtp.example.invalid", "smtp_port": "587",
            "smtp_username": "sender", "smtp_password": "", "smtp_from_email": "hello@example.invalid",
            "smtp_use_tls": "on", **changes}


def test_shared_settings_and_templates_without_enrollment_membership(stock, mailoutbox):
    stock.school.smtp_host = "smtp.example.invalid"
    stock.school.smtp_password = "stored-secret"
    stock.school.save()
    SchoolEmailTemplate.objects.create(school=stock.school, name="Existing Enrollment template",
                                      subject="Hello <script>alert(1)</script>", body="Private body")
    SchoolEmailTemplate.objects.create(school=stock.school, name="Booking template", purpose="bookings",
                                      subject="Hello <script>alert(1)</script>", body="Private booking body")
    SchoolEmailTemplate.objects.create(school=stock.school, purpose="bookings", name="Archived template", subject="Old",
                                      body="Old", is_active=False)
    other = School.objects.create(slug="other-email-school")
    SchoolEmailTemplate.objects.create(school=other, name="Other school secret", subject="Other", body="Other")
    response = stock.client.get(page(stock))
    assert response.status_code == 200
    assert b"Existing Enrollment template" not in response.content
    assert b"Booking template" in response.content
    assert b"Inactive" in response.content
    assert b"Other school secret" not in response.content
    assert b"stored-secret" not in response.content
    assert b'id="dashboard-location-filter"' not in response.content
    assert b"Private body" not in response.content
    assert b"<script>alert(1)</script>" not in response.content
    assert b"&lt;script&gt;" in response.content
    assert "no-store" in response["Cache-Control"]
    assert not mailoutbox


def test_save_preserves_password_and_unrelated_school_settings(stock, mailoutbox):
    stock.school.smtp_password = "stored-secret"
    stock.school.display_name = "Unchanged name"
    stock.school.save()
    response = stock.client.post(page(stock), data(display_name="Do not change"))
    assert response.status_code == 302
    stock.school.refresh_from_db()
    assert stock.school.smtp_host == "smtp.example.invalid"
    assert stock.school.smtp_port == 587
    assert stock.school.smtp_password == "stored-secret"
    assert stock.school.smtp_use_tls is True
    assert stock.school.display_name == "Unchanged name"
    assert not mailoutbox
    audit = AdminAuditLog.objects.latest("pk")
    assert audit.extra == {"name": "update_smtp"}
    assert "stored-secret" not in str(audit.changes)


def test_replace_password_and_restore_default(stock, mailoutbox):
    assert stock.client.post(page(stock), data(smtp_password=" new secret ", smtp_use_tls="")).status_code == 302
    stock.school.refresh_from_db()
    assert stock.school.smtp_password == " new secret "
    assert stock.school.smtp_use_tls is False
    assert stock.client.post(page(stock), {"action": "clear"}).status_code == 302
    stock.school.refresh_from_db()
    assert stock.school.smtp_host == stock.school.smtp_password == stock.school.smtp_username == stock.school.smtp_from_email == ""
    assert stock.school.smtp_port is None
    assert stock.school.smtp_use_tls is True
    assert AdminAuditLog.objects.latest("pk").extra == {"name": "clear_smtp"}
    assert not mailoutbox


@pytest.mark.parametrize("changes", [{"smtp_port": "0"}, {"smtp_port": "65536"}, {"smtp_port": "bad"},
                                    {"smtp_from_email": "invalid"}, {"smtp_host": "x" * 256}])
def test_invalid_values_are_not_saved_or_echoed(stock, changes):
    audit_count = AdminAuditLog.objects.count()
    response = stock.client.post(page(stock), data(smtp_password="new-secret", **changes))
    assert response.status_code == 400
    assert b"new-secret" not in response.content
    stock.school.refresh_from_db()
    assert not stock.school.smtp_host
    assert not stock.school.smtp_password
    assert AdminAuditLog.objects.count() == audit_count


@pytest.mark.parametrize("role,all_locations,active", [("scheduler", True, True), ("viewer", True, True),
    ("owner", False, True), ("owner", True, False)])
def test_only_schoolwide_active_owners_can_access(stock, role, all_locations, active):
    SchedulingMembership.objects.filter(user=stock.user).update(role=role, all_locations=all_locations, is_active=active)
    assert stock.client.get(page(stock)).status_code == 404
    assert stock.client.post(page(stock), data()).status_code == 404
    stock.school.refresh_from_db()
    assert not stock.school.smtp_host


def test_anonymous_unknown_action_and_environment_boundary(stock, client, settings):
    assert client.get(page(stock)).status_code == 302
    assert stock.client.post(page(stock), {"action": "unexpected"}).status_code == 400
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com"]
    assert stock.client.get(page(stock), HTTP_HOST="demo.example.com").status_code == 302
    assert stock.client.post(page(stock), data(), HTTP_HOST="demo.example.com").status_code in (302, 403, 404)
    stock.school.refresh_from_db()
    assert not stock.school.smtp_host
    assert stock.client.get(page(stock), HTTP_HOST="app.example.com").status_code == 200


def test_blank_port_uses_existing_transport_default(stock):
    form = SchoolEmailSettingsForm(data(smtp_port=""), instance=stock.school)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["smtp_port"] is None
