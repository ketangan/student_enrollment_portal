from io import StringIO

import pytest
from django.core.management import call_command, CommandError

from core.models import School, SchoolEmailTemplate
from scheduling import reservation_email as email
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email import data, url, template_url

pytestmark = pytest.mark.django_db


def test_settings_are_the_only_source_and_edits_and_deactivation_reach_composer(stock, booking, settings):
    settings.DEBUG = True
    assert set(email.draft_options(booking)) == {""}
    saved = stock.client.post(template_url(stock, "email_template_new"), {
        "name": "Guest follow-up", "booking_kind": "party", "booking_link": "guests",
        "subject": "Hello {{first_name}}", "body": "<p>Guests for {{booking_date}} at {{location}}</p>", "is_active": "on"})
    assert saved.status_code == 302
    template = SchoolEmailTemplate.objects.get()
    key = str(template.pk)
    settings_page = stock.client.get(template_url(stock, "email_settings"))
    page = stock.client.get(url(stock, booking))
    assert [row.pk for row in settings_page.context["email_templates"]] == [template.pk]
    assert set(page.context["email_options"]) == {"", key}
    assert page.context["email_options"][key]["subject"] == "Hello Host"
    assert "West studio" in page.context["email_options"][key]["body"]
    assert page.context["email_options"][key]["link_token"]
    edit_url = template_url(stock, "email_template_edit", pk=template.pk)
    changed = {"name": "Updated follow-up", "booking_kind": "both", "booking_link": "none",
        "subject": "Updated {{first_name}}", "body": "<p>Changed body</p>", "is_active": "on"}
    assert stock.client.post(edit_url, changed).status_code == 302
    option = stock.client.get(url(stock, booking)).context["email_options"][key]
    assert option["name"] == "Updated follow-up" and option["subject"] == "Updated Host"
    assert option["body"] == "<p>Changed body</p>" and not option["link_token"] and not option["link_hint"]
    changed.pop("is_active")
    assert stock.client.post(edit_url, changed).status_code == 302
    assert set(email.draft_options(booking)) == {""}


def test_unavailable_button_is_visible_but_disabled_and_forged_post_rejected(stock, booking, settings):
    settings.DEBUG = True
    template = SchoolEmailTemplate.objects.create(school=stock.school, purpose="bookings", name="Payment reminder",
        subject="Pay", body="<p>Pay</p>", booking_link="payment")
    key = str(template.pk)
    page = stock.client.get(url(stock, booking))
    assert page.context["email_options"][key]["unavailable"]
    assert f'value="{key}" disabled'.encode() in page.content
    response = stock.client.post(url(stock, booking), data(stock, booking, template=key))
    assert response.status_code == 400 and b"button is unavailable" in response.content


def test_explicit_standard_setup_is_school_scoped_additive_and_preserves_enrollment(stock):
    other = School.objects.create(slug="unchanged-enrollment")
    enrollment = SchoolEmailTemplate.objects.create(school=stock.school, name="Confirmation", subject="Enrollment", body="Original")
    before = list(SchoolEmailTemplate.objects.filter(school=other).values())
    out = StringIO()
    call_command("seed_booking_email_templates", school=stock.school.slug, stdout=out)
    assert "Added 3" in out.getvalue()
    templates = SchoolEmailTemplate.objects.filter(school=stock.school, purpose="bookings")
    assert templates.count() == 3
    templates.filter(name="Confirmation").update(subject="Staff edited", is_active=False)
    call_command("seed_booking_email_templates", school=stock.school.slug, stdout=out)
    assert templates.count() == 3 and templates.get(name="Confirmation").subject == "Staff edited"
    assert not templates.get(name="Confirmation").is_active
    enrollment.refresh_from_db()
    assert enrollment.subject == "Enrollment" and enrollment.body == "Original"
    assert list(SchoolEmailTemplate.objects.filter(school=other).values()) == before
    for slug in (other.slug, "missing-school"):
        with pytest.raises(CommandError):
            call_command("seed_booking_email_templates", school=slug)
