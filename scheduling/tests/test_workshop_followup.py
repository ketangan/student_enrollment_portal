from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.http import Http404, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from scheduling import inventory, party_notifications, workshop_followup
from scheduling.message_models import BookingMessage
from scheduling.request_forms import request_forms
from scheduling.requests import catalog_for
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_workshop_selections import pending_booking

pytestmark = pytest.mark.django_db


def data(s):
    return {"selection-design": s.cat.pk, "selection-color": s.blue.pk,
            "selection-guest_name": "Alex", "selection-quantity": "999"}


def test_booking_level_choice_controls_every_participant(stock):
    s = stock
    s.offering.allow_deferred_selections = True
    body = QueryDict(mutable=True)
    body.update(choose_when="later", purchaser_name="Host", purchaser_email="host@example.invalid",
        **{"participants-TOTAL_FORMS": "6", "participants-INITIAL_FORMS": "0"})
    _, forms = request_forms(s.session, catalog_for(s.offering), body)
    assert forms.is_valid(), forms.errors
    assert [f.cleaned_data["name"] for f in forms] == [f"Participant {n}" for n in range(1, 7)]
    assert all(f.participant_snapshot()["selections_deferred"] for f in forms)
    body["choose_when"] = "now"
    body["participants-0-selections_deferred"] = "on"
    assert not request_forms(s.session, catalog_for(s.offering), body)[1].is_valid()
    body["choose_when"] = "later"
    s.offering.allow_deferred_selections = False
    assert not request_forms(s.session, catalog_for(s.offering), body)[1].is_valid()


def test_private_link_scopes_token_and_preserves_money(stock):
    s = stock
    item, person = pending_booking(s, mixed=True)
    token = workshop_followup.token_for(item)
    for slug, value in [(s.school.slug, token + "bad"), ("other-school", token)]:
        with pytest.raises(Http404):
            workshop_followup.resolve(slug, value)
    before = (item.quantity, item.order.total_cents, item.order.entries.count())
    saved, form = workshop_followup.save(s.school.slug, token, person.pk, data(s))
    assert not form.errors
    person.refresh_from_db()
    assert person.name == "Alex" and not person.selections_deferred
    assert inventory.reserved(s.level) == 2
    assert (saved.quantity, saved.order.total_cents, saved.order.entries.count()) == before
    with pytest.raises(ValidationError, match="already been saved"):
        workshop_followup.save(s.school.slug, token, person.pk, data(s))
    assert inventory.reserved(s.level) == 2


@pytest.mark.parametrize("condition", ["prepared", "started", "canceled", "held"])
def test_customer_cutoff_is_checked_inside_save(stock, condition):
    s = stock
    item, person = pending_booking(s)
    if condition == "prepared":
        item.prepared_at = timezone.now()
    elif condition == "started":
        item.starts_at = timezone.now() - timedelta(seconds=1)
    else:
        item.state = condition
    item.save()
    with pytest.raises(ValidationError, match="closed"):
        workshop_followup.save(s.school.slug, workshop_followup.token_for(item), person.pk, data(s))
    assert inventory.reserved(s.level) == 0


def test_partial_completion_and_stock_failure_rollback(stock):
    s = stock
    item, person = pending_booking(s)
    other, stranger = pending_booking(s)
    token = workshop_followup.token_for(item)
    with pytest.raises(Http404):
        workshop_followup.save(s.school.slug, token, stranger.pk, data(s))
    _, form = workshop_followup.save(s.school.slug, token, person.pk, {})
    assert form.errors
    s.level.on_hand = 0
    s.level.save()
    with pytest.raises(ValidationError):
        workshop_followup.save(s.school.slug, token, person.pk, data(s))
    person.refresh_from_db()
    assert person.name != "Alex" and person.selections_deferred


def test_public_form_csrf_errors_and_success(stock):
    s = stock
    item, person = pending_booking(s)
    url = reverse("scheduling:workshop_selections", kwargs={"school_slug": s.school.slug,
        "token": workshop_followup.token_for(item)})
    client = Client()
    response = client.get(url)
    assert response.status_code == 200 and b"Choose your designs" in response.content
    assert response["Referrer-Policy"] == "same-origin"
    assert client.get(url + "?participant=999999").status_code == 404
    assert client.post(url, {}).status_code == 400
    assert Client(enforce_csrf_checks=True).post(url, {"participant": person.pk, **data(s)}).status_code == 403
    assert client.post(url, {"participant": person.pk}).status_code == 400
    secure_client = Client(enforce_csrf_checks=True)
    secure_client.get(url)
    assert secure_client.post(url, {"participant": person.pk, **data(s),
        "csrfmiddlewaretoken": secure_client.cookies["csrftoken"].value}, HTTP_ORIGIN="http://testserver").status_code == 302
    assert b"All participants have chosen" in client.get(url).content
    assert b"Save selection" in client.get(url + f"?participant={person.pk}").content
    assert client.post(url, {"participant": person.pk, **data(s)}).status_code == 400


def test_confirmation_uses_existing_worker_and_private_link(stock, settings):
    s = stock
    item, person = pending_booking(s)
    party_notifications.capture_for_reservation(item.pk)
    assert not BookingMessage.objects.exists()
    s.profile.workshop_notifications_enabled = True
    s.profile.save()
    call_command("process_party_notifications", school=s.school.slug)
    call_command("process_party_notifications", school=s.school.slug)
    message = BookingMessage.objects.get(reservation=item)
    assert message.state == "draft"
    assert "Choose your designs" in message.body
    assert "/workshop-selections/" in message.body
    assert party_notifications.deliverable(message)
    item.state = "canceled"
    item.save()
    assert not party_notifications.deliverable(message)


def test_followup_preserves_other_pending_participants(stock):
    from scheduling.tests.test_inventory import request as make_request, confirm
    from scheduling import reservations
    s = stock
    source = make_request(s, 2)
    source.participants.update(selections=[], selections_deferred=True)
    item = reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id,
        total_cents=13000, deposit_cents=5000)
    confirm(s, item)
    item.refresh_from_db()
    token = workshop_followup.token_for(item)
    workshop_followup.save(s.school.slug, token, source.participants.first().pk, data(s))
    item.refresh_from_db()
    assert item.snapshot["workshop_selections_pending"]
    assert source.participants.filter(selections_deferred=True).count() == 1
    assert inventory.reserved(s.level) == 1
    assert item.order.total_cents == 13000


@pytest.mark.parametrize("demo,environment,expected", [(True,"live",0), (False,"test",0), (False,"live",1)])
def test_workshop_email_worker_environment_and_retry_safety(stock, settings, monkeypatch, demo, environment, expected):
    s = stock
    item, _ = pending_booking(s)
    s.profile.workshop_notifications_enabled = True
    s.profile.save()
    s.school.is_demo = demo
    s.school.save()
    item.order.environment = environment
    item.order.save()
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda *args: Transport())
    call_command("process_party_notifications", all_schools=True)
    call_command("process_party_notifications", all_schools=True)
    assert len(sent) == expected
    if expected:
        message = BookingMessage.objects.get()
        assert message.state == "sent" and message.recipient == item.workshop_request.purchaser_email
        assert "Choose your designs" in message.body
        s.profile.workshop_notifications_enabled = False
        s.profile.save()
        assert not party_notifications.deliverable(message)


def test_email_after_staff_completion_still_allows_edits(stock):
    from scheduling.tests.test_workshop_selections import save
    s = stock
    item, person = pending_booking(s)
    save(s, item, person)
    s.profile.workshop_notifications_enabled = True
    s.profile.save()
    message = party_notifications.capture_for_reservation(item.pk)
    assert "Choose your designs" in message.body
    assert "workshop is confirmed" in message.body


def test_public_link_enforces_portal_and_closed_state(stock, settings):
    s = stock
    item, person = pending_booking(s)
    url = reverse("scheduling:workshop_selections", kwargs={"school_slug":s.school.slug,"token":workshop_followup.token_for(item)})
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    response = Client().get(url, HTTP_HOST="demo.example.com")
    assert response.status_code == 302
    assert response.url == "https://app.example.com" + url
    assert Client().post(url, {"participant": person.pk, **data(s)},
        HTTP_HOST="demo.example.com").status_code == 403
    person.refresh_from_db()
    assert person.selections_deferred
    item.prepared_at = timezone.now()
    item.save()
    client = Client()
    assert b"Online selections are closed" in client.get(url).content
    assert client.post(url,{"participant":person.pk, **data(s)}).status_code == 400
