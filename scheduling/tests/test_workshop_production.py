"""Real-school customer intake with local-only provider responses, never live I/O."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from django.db import close_old_connections
from django.test import Client, RequestFactory
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentAccount, PaymentAttempt, PaymentOrder
from scheduling import workshop_experience as experience, requests, reservations
from scheduling.checkout import payment_token
from scheduling.experience_demo import config as demo_config, service
from scheduling.integrations import vault
from scheduling.models import (ChoiceGroup, ChoiceOption, Location, Reservation, SchedulingMembership,
                               SquareConnection, SquareLocationMapping, WorkshopRequest)
from .test_inventory import stock, url
from .test_square_payments import payment_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def live(stock, settings, monkeypatch):
    s = stock
    s.profile.payment_provider = "square"
    s.profile.save()
    s.offering.price_per_participant = Decimal("65")
    s.offering.deposit_per_participant = Decimal("20")
    s.offering.save()
    s.cat.image_url = "/static/scheduling/catalog/plushies/cat.png"
    s.cat.save()
    settings.BOOKINGS_INTEGRATION_KEYS = [Fernet.generate_key()]
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = True
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_ID = "sq0idp-live-app"
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET = "local-only-secret"
    settings.PAYMENTS_SQUARE_LIVE_OAUTH_REDIRECT_URI = "https://testserver/bookings/square/oauth/callback/"
    settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET = "local-hook-secret"
    settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_URL = "https://testserver/bookings/square/live/webhook/"
    s.connection = SquareConnection.objects.create(profile=s.profile, environment="live", status="connected",
        merchant_id="LOCAL_SELLER", application_id=settings.PAYMENTS_SQUARE_LIVE_APPLICATION_ID,
        expires_at=timezone.now() + timedelta(days=10))
    s.connection.credential_ciphertext = vault.seal(s.connection, json.dumps({"access_token": "local-only-token",
        "refresh_token": "local-refresh", "merchant_id": s.connection.merchant_id,
        "application_id": s.connection.application_id}))
    s.connection.save()
    s.account = PaymentAccount.objects.create(school=s.school, provider="square", scope="connected",
        environment="live", reference=s.connection.merchant_id, location_reference="LOCAL_LOCATION", enabled=True)
    s.mapping = SquareLocationMapping.objects.create(connection=s.connection, location=s.location,
        account=s.account, environment="live")
    s.configuration = {"scheduling": {"customer_experience": {"enabled": True}}}
    monkeypatch.setattr(experience, "load_school_config", lambda slug: SimpleNamespace(raw=s.configuration) if slug == s.school.slug else None)
    # A production request must never read a demo YAML file or require a demo owner.
    monkeypatch.setattr(demo_config, "configuration", Mock(side_effect=AssertionError("Demo loader used")))
    monkeypatch.setattr(service, "actor", Mock(side_effect=AssertionError("Demo actor used")))
    s.browser = Client()
    return s


def reviewed(s, *, count=1, browser=None):
    browser = browser or s.browser
    path = url(s, "experience_choose", pk=s.session.pk)
    page = browser.get(path)
    assert page.status_code == 200
    data = {"request_token": page.context["request_token"], "purchaser_name": "Booking parent",
        "purchaser_email": "test@example.invalid", "policy_ack": "on", "action": "review",
        "participants-TOTAL_FORMS": str(count), "participants-INITIAL_FORMS": "0"}
    for index in range(count):
        data[f"participants-{index}-name"] = f"Maker {index + 1}"
        for group in requests.catalog_for(s.offering):
            data[f"participants-{index}-choice_{group.pk}"] = str(group.active_options[0].pk)
    response = browser.post(path, data)
    assert response.status_code == 200, response.content.decode()
    return path, {**data, "action": "checkout", "review_token": response.context["review_token"]}


def held(s):
    path, data = reviewed(s)
    response = s.browser.post(path, data)
    assert response.status_code == 302
    return WorkshopRequest.objects.get(), response.url, path, data


def test_real_school_complete_flow_no_demo_config_or_owner(live, monkeypatch):
    s = live
    SchedulingMembership.objects.filter(profile=s.profile).delete()
    page = s.browser.get(url(s, "experience_start"))
    assert page.status_code == 200 and page.context["sessions"][0]["places"] == 100
    assert s.browser.get(url(s, "public_schedule")).url == url(s, "experience_start")
    choice = s.browser.get(url(s, "experience_choose", pk=s.session.pk))
    assert choice.context["catalog_data"]["designs"][0]["image_url"] == s.cat.image_url
    item, receipt, path, data = held(s)
    assert "customer_experience" in item.session_snapshot and "experience_demo" not in item.session_snapshot
    assert item.reservation.order.environment == "live"
    assert item.reservation.order.account == s.account
    assert item.reservation.stock_allocations.get().quantity == 1
    assert AdminAuditLog.objects.filter(model_label="scheduling.reservation", actor__isnull=True).exists()
    assert s.browser.post(path, data).url == receipt
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1
    assert b"Simulate" not in s.browser.get(receipt).content
    assert s.browser.post(receipt, {"action": "complete"}).status_code == 400
    assert not item.reservation.order.entries.exists()
    checkout = s.browser.post(receipt, {"action": "payment_checkout"})
    assert checkout.status_code == 302
    assert b"web.squarecdn.com" in s.browser.get(checkout.url).content
    attempt = PaymentAttempt.objects.get()
    assert s.browser.get(receipt + "?checkout_return=1").status_code == 200
    item.reservation.refresh_from_db()
    assert item.reservation.state == "held" and not item.reservation.order.entries.exists()
    from core.payments import square
    from scheduling.square_checkout import submit
    evidence = payment_for(attempt)
    evidence["application_details"]["application_id"] = s.connection.application_id
    monkeypatch.setattr(square, "request", Mock(return_value={"payment": evidence}))
    submit(attempt, "cnon:local-only")
    response = s.browser.get(receipt + "?checkout_return=1")
    assert b"Booking confirmed" in response.content
    assert b"Pay remaining balance: $45.00" in response.content
    assert item.reservation.order.entries.count() == 1
    calendar = s.browser.get(url(s, "experience_calendar", public_id=item.public_id))
    assert b"Workshop reservation." in calendar.content and b"Not a real reservation" not in calendar.content
    assert Client().get(receipt).status_code == 404


@pytest.mark.parametrize("change", ["missing", "disabled", "wrong_location", "sandbox", "webhook", "activation", "stripe"])
def test_payment_preflight_blocks_before_intake(live, settings, change):
    s = live
    if change == "missing":
        s.mapping.delete()
    elif change == "disabled":
        s.account.enabled = False; s.account.save()
    elif change == "wrong_location":
        s.mapping.location = Location.objects.create(profile=s.profile, name="Other room"); s.mapping.save()
    elif change == "sandbox":
        s.mapping.environment = "test"; s.mapping.save()
    elif change == "webhook":
        settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET = ""
    elif change == "activation":
        settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    else:
        s.profile.payment_provider = "stripe"; s.profile.save()
    start = s.browser.get(url(s, "experience_start"))
    assert start.context["sessions"][0]["places"] == 0
    assert b"temporarily unavailable" in start.content
    assert s.browser.get(url(s, "experience_choose", pk=s.session.pk)).status_code == 503
    assert not WorkshopRequest.objects.exists() and not PaymentOrder.objects.exists()


@pytest.mark.parametrize("change", ["disabled", "inactive", "capability", "optout", "simulate"])
def test_opt_in_and_simulation_guards(live, change):
    s = live
    path, data = reviewed(s)
    if change == "disabled":
        s.profile.enabled = False; s.profile.save()
    elif change == "inactive":
        s.school.is_active = False; s.school.save()
    elif change == "capability":
        s.profile.capabilities = []; s.profile.save()
    elif change == "optout":
        s.configuration["scheduling"]["customer_experience"]["enabled"] = False
    else:
        data["simulate_checkout"] = "1"
    assert s.browser.post(path, data).status_code == 404
    assert not WorkshopRequest.objects.exists()


def test_pause_keeps_existing_links_but_stops_new_bookings(live):
    s = live
    item, receipt, path, data = held(s)
    s.profile.native_reservations_enabled = False; s.profile.save()
    assert b"temporarily paused" in s.browser.get(url(s, "experience_start")).content
    assert s.browser.get(receipt).status_code == 200
    assert s.browser.post(path, data).status_code == 409
    assert Reservation.objects.count() == 1
    with pytest.raises(Http404):
        service.simulate(s.profile, item, {}, "complete")
    assert not service.has_simulated_payment(s.profile, item.reservation)


def test_capacity_and_inventory_rechecked_and_rollback(live):
    s = live
    path, data = reviewed(s, count=2)
    s.session.capacity = 1; s.session.save()
    assert s.browser.post(path, data).status_code == 400
    assert not WorkshopRequest.objects.exists()
    s.session.capacity = 10; s.session.save()
    path, data = reviewed(s)
    s.level.on_hand = 0; s.level.save()
    assert s.browser.post(path, data).status_code == 400
    assert not WorkshopRequest.objects.exists() and not PaymentOrder.objects.exists()


def test_edit_and_saved_payment_review_use_same_non_demo_flow(live):
    s = live
    item, receipt, _, _ = held(s)
    review_url = url(s, "customer_payment", token=payment_token(item.reservation))
    page = s.browser.get(review_url)
    assert page.status_code == 200 and page.context["saved_review"]
    edit_url = page.context["customer_edit_url"]
    choice = s.browser.get(edit_url)
    assert choice.status_code == 200 and choice.context["catalog_data"]["design_group"] == "Design"
    assert s.browser.get(edit_url + "?change_workshop=1").status_code == 200
    from scheduling.customer_edits import workshop_data
    data = workshop_data(item.reservation, s.session)
    data.update({"participants-0-name": "Updated maker", "action": "review", "edit_revision": choice.context["edit_revision"]})
    review = s.browser.post(edit_url, data)
    assert review.status_code == 200
    data.update({"review_token": review.context["review_token"], "action": "checkout"})
    assert s.browser.post(edit_url, data).status_code == 302
    item.refresh_from_db()
    assert "customer_experience" in item.session_snapshot
    assert item.participants.get().name == "Updated maker"
    assert s.browser.get(receipt).status_code == 200
    assert s.browser.get(review_url).context["rows"][0]["name"] == "Updated maker"


def test_preflight_readonly_success_and_errors(live):
    s = live
    output = StringIO()
    call_command("check_workshop_launch", s.school.slug, stdout=output)
    assert "Configuration checks passed" in output.getvalue()
    assert not WorkshopRequest.objects.exists()
    with pytest.raises(CommandError):
        call_command("check_workshop_launch", "missing")
    s.school.is_demo = True; s.school.save()
    with pytest.raises(CommandError):
        call_command("check_workshop_launch", s.school.slug)
    s.school.is_demo = False; s.school.save()
    s.configuration["scheduling"]["customer_experience"]["enabled"] = False
    s.session.is_published = False; s.session.save()
    with pytest.raises(CommandError, match="No upcoming"):
        call_command("check_workshop_launch", s.school.slug)
    s.session.is_published = True; s.session.save()
    s.mapping.delete()
    s.blue.is_active = False; s.blue.save()
    with pytest.raises(CommandError, match="unavailable catalog"):
        call_command("check_workshop_launch", s.school.slug)


def test_config_boundaries_and_renamed_dimensions(live, monkeypatch):
    s = live
    s.design.name = "Your creation"; s.design.save()
    s.color.name = "Fabric"; s.color.save()
    age = ChoiceGroup.objects.create(catalog=s.catalog, name="Age", position=2)
    ChoiceOption.objects.create(group=age, label="Adult")
    from scheduling import participant_policy
    monkeypatch.setattr(participant_policy, "load_school_config", lambda slug: SimpleNamespace(raw={"scheduling": {
        "participant_age": {"minimum": 7, "choice_group": "Age", "adult_label": "Adult"}}}))
    page = s.browser.get(url(s, "experience_choose", pk=s.session.pk))
    config = page.context["catalog_data"]
    assert (config["design_group"], config["color_group"], config["age_group"]) == ("Your creation", "Fabric", "Age")
    held(s)
    s.offering.inventory_catalog = None
    s.offering.save()
    _, base = experience.context(RequestFactory().get("/"), s.school.slug)
    assert experience.offering_config(s.profile, base, s.offering)["design_group"] == ""
    with pytest.raises(Http404):
        experience.offering_config(SimpleNamespace(pk=-1), base, s.offering)
    for value in ([], {"enabled": "yes"}):
        s.configuration["scheduling"]["customer_experience"] = value
        with pytest.raises(ValidationError):
            experience.available(s.profile)
    other = School.objects.create(slug="untouched-school")
    assert experience.settings_for(other) == {}


def test_test_account_never_becomes_production_fallback(live, monkeypatch):
    s = live
    monkeypatch.setattr(reservations, "booking_account", lambda *args: SimpleNamespace(school_id=s.school.pk, environment="test"))
    with pytest.raises(ValidationError, match="live payment"):
        experience.live_account(s.profile, s.location)
    assert s.browser.get(url(s, "experience_choose", pk=s.session.pk)).status_code == 503


def test_service_rejects_foreign_session_offline_and_changed_quote(live):
    s = live
    item, _, _, data = held(s)
    _, config = experience.context(RequestFactory().get("/"), s.school.slug)
    from django.http import QueryDict
    body = QueryDict(mutable=True); body.update(data)
    with pytest.raises(ValidationError, match="Online payment"):
        service.begin(s.profile, s.session, body, item.browser_digest, {**config, "stripe_checkout": False})
    item.reservation.order.total_cents += 1
    item.reservation.order.save()
    with pytest.raises(ValidationError, match="different quote"):
        service.begin(s.profile, s.session, body, item.browser_digest, config)
    foreign = SimpleNamespace(offering=SimpleNamespace(location=SimpleNamespace(profile_id=-1)))
    with pytest.raises(Http404):
        service.begin(s.profile, foreign, body, item.browser_digest, config)
    assert PaymentOrder.objects.count() == 1


def test_preflight_reports_invalid_quote(live, monkeypatch):
    monkeypatch.setattr(reservations, "workshop_quote", Mock(side_effect=ValidationError("Deposit needs review")))
    with pytest.raises(CommandError, match="Deposit needs review"):
        call_command("check_workshop_launch", live.school.slug)


def test_production_catalog_does_not_read_legacy_artwork(live):
    from scheduling.inventory_forms import party_catalog_data
    catalog = party_catalog_data(live.offering)
    assert catalog["designs"][0]["image_url"] == live.cat.image_url
    assert catalog["image"] == ""


@pytest.mark.django_db(transaction=True)
def test_concurrent_customer_submissions_cannot_oversell(live):
    s = live
    s.session.capacity = 1; s.session.save()
    clients = [Client(), Client()]
    submissions = [reviewed(s, browser=browser) for browser in clients]
    def submit(index):
        close_old_connections()
        try:
            path, data = submissions[index]
            return clients[index].post(path, data).status_code
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(submit, range(2))) == [302, 400]
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1
