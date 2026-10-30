from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404, QueryDict
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership
from core.payments.models import LedgerEntry, PaymentOrder
from scheduling import requests
from scheduling.experience_demo import config, service
from scheduling.models import Camp, Location, Offering, Reservation, SchedulingMembership, SchedulingProfile, Session, WorkshopRequest

pytestmark = pytest.mark.django_db
SLUG = "plushumi-demo"


@pytest.fixture
def local(monkeypatch, settings):
    monkeypatch.setattr(config, "settings", SimpleNamespace(DEBUG=True, BASE_DIR=settings.BASE_DIR,
        DATABASES={"default": {"NAME": config.PREVIEW_DATABASE, "HOST": "127.0.0.1"}}))


@pytest.fixture
def demo(local):
    call_command("seed_booking_experience_demo", SLUG, password="admin")
    return SchedulingProfile.objects.get(school__slug=SLUG)


@pytest.fixture
def browser():
    return Client(HTTP_HOST="127.0.0.1:8002")


def url(name, **kwargs):
    return reverse("scheduling:experience_"+name, kwargs={"school_slug": SLUG, **kwargs})


def form_data(browser, demo, count=2, session=None):
    session = session or service.sessions(demo).first()
    response = browser.get(url("choose", pk=session.pk))
    assert response.status_code == 200
    data = {"request_token": response.context["request_token"], "purchaser_name": "Jordan Demo",
        "purchaser_email": "jordan@example.invalid", "policy_ack": "on", "participants-TOTAL_FORMS": str(count),
        "participants-INITIAL_FORMS": "0", "action": "review"}
    for index in range(count):
        data[f"participants-{index}-name"] = f"Participant {index+1}"
        for group in requests.catalog_for(session.offering):
            data[f"participants-{index}-choice_{group.pk}"] = str(group.active_options[index % len(group.active_options)].pk)
    return session, data


def test_policy_snapshot_and_stale_review(demo, browser, monkeypatch):
    from scheduling import booking_policies
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    original = booking_policies.notice(demo.school, "workshop")
    monkeypatch.setattr(service, "notice", lambda *args: {**original, "text": "Changed policy"})
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 400
    assert not WorkshopRequest.objects.exists()
    monkeypatch.setattr(service, "notice", booking_policies.notice)
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 302
    item = WorkshopRequest.objects.get()
    assert item.session_snapshot["experience_demo"]["published_policy"] == original
    monkeypatch.setattr(service, "notice", lambda *args: {**original, "text": "Later policy"})
    item.refresh_from_db()
    assert item.session_snapshot["experience_demo"]["published_policy"] == original


def test_customer_footer_returns_to_options_from_every_booking_step(demo, browser):
    start = browser.get(url("start"))
    session, data = form_data(browser, demo, count=1)
    choice = browser.get(url("choose", pk=session.pk))
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    checkout = browser.post(url("choose", pk=session.pk), data)
    handoff = browser.get(checkout.url)
    assert browser.post(checkout.url, {"action": "complete"}).status_code == 302
    confirmation = browser.get(checkout.url)
    link = f'<a href="{reverse("booking_integration_preview")}" target="_top">Back to booking options</a>'
    html = SimpleTestCase()
    for response in (start, choice, review, handoff, confirmation):
        assert response.status_code == 200
        html.assertInHTML(link, response.content.decode(), count=1)
        assert b"Parties &amp; camps" not in response.content
        assert b"Studio team" not in response.content
    options = browser.get(reverse("booking_integration_preview"))
    assert options.status_code == 200
    assert b"Open booking page" in options.content and b"View popup preview" in options.content
    assert reverse("scheduling:party_start", kwargs={"school_slug": "plushumi-demo"}).encode() in options.content


@pytest.mark.parametrize('blank_details', [False, True, 'group'])
def test_deferred_selection_survives_review_checkout_and_confirmation(demo, browser, blank_details):
    session = service.sessions(demo).filter(offering__inventory_catalog__isnull=False,
        starts_at__gt=timezone.now() + timedelta(days=7)).first()
    session.offering.allow_deferred_selections = True
    session.offering.save()
    session, data = form_data(browser, demo, count=6 if blank_details == 'group' else 1, session=session)
    data['participants-0-selections_deferred'] = 'on'
    for group in requests.catalog_for(session.offering):
        if group.tracks_stock or (blank_details and not getattr(group, 'is_participant_age', False)):
            data.pop(f'participants-0-choice_{group.pk}')
    if blank_details:
        data['participants-0-name'] = ''
    if blank_details == 'group':
        data['choose_when'] = 'later'
        for index in range(6):
            data[f'participants-{index}-name'] = ''
            for group in requests.catalog_for(session.offering):
                if not getattr(group, 'is_participant_age', False):
                    data.pop(f'participants-{index}-choice_{group.pk}', None)
    review = browser.post(url('choose', pk=session.pk), data)
    assert review.status_code == 200 and b'Choosing at the studio' in review.content
    data.update(action='checkout', review_token=review.context['review_token'])
    checkout = browser.post(url('choose', pk=session.pk), data)
    assert checkout.status_code == 302
    assert browser.post(checkout.url, {'action': 'complete'}).status_code == 302
    confirmation = browser.get(checkout.url)
    assert b'Choosing at the studio' in confirmation.content
    item = WorkshopRequest.objects.get(purchaser_name='Jordan Demo')
    assert all(p.selections_deferred for p in item.participants.all())
    if blank_details:
        assert item.participants.first().name == 'Participant 1'
        assert [(s['group'], s['option']) for s in item.participants.first().selections] == [('Age', '7')]
    if blank_details == 'group':
        assert item.participants.count() == 6
        assert b'Choose your designs' in confirmation.content
    assert item.reservation.state == 'confirmed'
    assert not item.reservation.stock_allocations.exists()


def test_pasadena_sample_workshop_is_additive_and_repeatable(demo):
    from scheduling.models import ChoiceOption
    color = Offering.objects.filter(location__profile=demo).exclude(inventory_catalog=None).first().inventory_catalog.choice_groups.get(name="Color")
    ChoiceOption.objects.create(group=color, label="Unconfigured color", swatch="#123456", position=0)
    existing = set(Reservation.objects.values_list("pk", flat=True))
    call_command("seed_pasadena_workshop")
    workshop = Offering.objects.get(location__profile=demo, name="Cozy Creations")
    assert workshop.location.name == "Pasadena, CA"
    assert workshop.sessions.count() == 3
    bookings = Reservation.objects.filter(workshop_request__session__offering=workshop)
    assert bookings.count() == 3 and bookings.filter(state="confirmed").count() == 3
    assert bookings.filter(prepared_at__isnull=False).count() == 1
    assert all(b.stock_allocations.exists() for b in bookings)
    assert existing.issubset(set(Reservation.objects.values_list("pk", flat=True)))
    before = list(bookings.order_by("pk").values())
    call_command("seed_pasadena_workshop")
    assert list(bookings.order_by("pk").values()) == before
    assert len([r for r in service.session_rows(demo) if r["workshop"] == workshop.name]) == 3


def test_pasadena_seed_refuses_nonlocal_database(monkeypatch):
    monkeypatch.setattr(config, "local_database", lambda: False)
    with pytest.raises(CommandError):
        call_command("seed_pasadena_workshop")


def test_pasadena_seed_refuses_connected_school(demo):
    demo.school.smtp_host = "smtp.example.invalid"
    demo.school.save(update_fields=["smtp_host"])
    with pytest.raises(CommandError):
        call_command("seed_pasadena_workshop")
    assert not Offering.objects.filter(name="Cozy Creations").exists()


def test_pasadena_seed_rolls_back_without_recipes(demo):
    from scheduling.models import MaterialRequirement
    MaterialRequirement.objects.filter(variant__catalog__profile=demo).delete()
    with pytest.raises(CommandError, match="recipes"):
        call_command("seed_pasadena_workshop")
    assert not Offering.objects.filter(name="Cozy Creations").exists()


def test_review_and_checkout_show_each_participant_preview(demo, browser, monkeypatch):
    from scheduling import customer_experience
    monkeypatch.setattr(customer_experience, "customer_copy", lambda school: {})
    from scheduling.models import CatalogVariant
    session, data = form_data(browser, demo)
    groups = requests.catalog_for(session.offering)
    selected = {int(data[f"participants-0-choice_{g.pk}"]) for g in groups}
    variant = next(v for v in CatalogVariant.objects.filter(catalog=session.offering.inventory_catalog)
        if set(v.option_ids).issubset(selected))
    variant.image_url = "https://example.com/white-unicorn.jpg"
    variant.save(update_fields=["image_url"])
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200
    assert review.content.count(b'class="selection-thumbnail"') == 2
    assert review.context["rows"][0]["preview"]["image"] == variant.image_url
    assert review.context["rows"][0]["preview"]["exact"]
    assert review.context["rows"][1]["preview"]["x"]
    data.update(action="checkout", review_token=review.context["review_token"])
    checkout = browser.post(url("choose", pk=session.pk), data)
    handoff = browser.get(checkout.url)
    assert handoff.status_code == 200
    assert handoff.content.count(b'class="selection-thumbnail"') == 2
    assert b'https://example.com/white-unicorn.jpg' in handoff.content
    assert handoff.context["people"][0]["preview"] == review.context["rows"][0]["preview"]
    assert browser.post(checkout.url, {"action": "complete"}).status_code == 302
    assert browser.get(checkout.url).content.count(b'class="selection-thumbnail"') == 2


def test_preview_missing_design_and_custom_photo_fallback(demo):
    offering = service.sessions(demo).first().offering
    settings = config.configuration(SLUG)
    rows = [{"selections": []}, {"selections": [
        {"group": "Design", "option": "Custom toy", "option_id": -1, "image_url": "https://example.com/custom.jpg"},
        {"group": "Color", "option": "Coral", "swatch": "#ffaaaa"}]}]
    service.selection_previews(rows, offering, settings)
    assert rows[0]["preview"]["image"] == "" and rows[0]["preview"]["x"] == ""
    assert rows[1]["preview"]["image"] == "https://example.com/custom.jpg"
    assert rows[1]["preview"]["swatch"] == "#ffaaaa" and not rows[1]["preview"]["exact"]


def test_admin_created_workshop_is_bookable_without_unpaid_intake(demo, browser):
    staff = Client(HTTP_HOST="localhost:8002")
    staff.force_login(get_user_model().objects.get(username="plushie_admin"))
    location = Location.objects.get(profile=demo, name="Charlotte, NC")
    original = Offering.objects.filter(location=location).first()
    def admin_url(name, **kwargs):
        return reverse("scheduling:" + name, kwargs={"school_slug": SLUG, **kwargs})
    assert staff.post(admin_url("workshop_new"), {"name": "Young Horizons", "location": location.pk,
        "inventory_catalog": original.inventory_catalog_id}).status_code == 302
    offering = Offering.objects.get(location=location, name="Young Horizons")
    for days in (40, 42):
        start = (timezone.now() + timedelta(days=days)).strftime("%Y-%m-%dT10:00")
        assert staff.post(admin_url("workshop_detail", pk=offering.pk), {
            "session_id": "new", "starts_at": start, "duration_minutes": 90, "capacity": 4}).status_code == 302
    sessions = list(offering.sessions.order_by("starts_at"))
    for session in sessions:
        assert staff.post(admin_url("session_publish", pk=session.pk), {"action": "publish"}).status_code == 302
    assert staff.post(admin_url("workshop_publish", pk=offering.pk), {"action": "publish"}).status_code == 302
    offering.refresh_from_db()
    assert offering.is_published and not offering.accepts_requests and not offering.accepts_party_requests
    listing = browser.get(url("start"))
    rows = [row for row in listing.context["sessions"] if row["id"] in {s.pk for s in sessions}]
    assert len(rows) == 2
    assert all(row["workshop"] == "Young Horizons" and row["location_id"] == location.pk
        and row["workshop_id"] == offering.pk and row["duration_minutes"] == 90 for row in rows)
    assert b'id="workshop"' in listing.content
    for session in sessions:
        assert browser.get(url("choose", pk=session.pk)).status_code == 200
        assert browser.get(admin_url("workshop_request", pk=session.pk)).status_code == 404
    session, data = form_data(browser, demo, count=1, session=sessions[0])
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200 and b"Young Horizons" in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    item = WorkshopRequest.objects.get(session=session)
    assert browser.post(result.url, {"action": "complete"}).status_code == 302
    item.reservation.refresh_from_db()
    assert item.reservation.state == "confirmed"
    assert item.session_snapshot["workshop"] == "Young Horizons"
    assert browser.post(url("choose", pk=session.pk), data).status_code == 302
    assert WorkshopRequest.objects.filter(session=session).count() == 1
    assert item.reservation.order.entries.count() == 1
    prep = staff.get(url("preparation"), {"session": session.pk})
    assert prep.status_code == 200 and prep.context["selected"].pk == session.pk
    assert any(row["booking"].pk == item.pk for row in prep.context["rows"])


@pytest.mark.parametrize("guard", ["workshop_draft", "session_draft", "past", "empty_choices", "paused", "capability", "disabled"])
def test_native_intake_rechecks_availability_without_unpaid_flag(demo, browser, guard):
    session, data = form_data(browser, demo, count=1)
    session.offering.accepts_requests = False
    session.offering.save()
    body = QueryDict(mutable=True)
    body.update(data)
    body = service.payload(body, requests.catalog_for(session.offering))
    browser_key = signing.loads(data["request_token"], salt=requests.TOKEN_SALT)["browser"]
    if guard == "workshop_draft":
        session.offering.is_published = False; session.offering.save()
    elif guard == "session_draft":
        session.is_published = False; session.save()
    elif guard == "past":
        Session.objects.filter(pk=session.pk).update(starts_at=timezone.now()-timedelta(hours=2))
    elif guard == "empty_choices":
        requests.catalog_for(session.offering)[0].options.update(is_active=False)
    elif guard == "paused":
        demo.native_reservations_enabled = False; demo.save()
    elif guard == "capability":
        demo.capabilities = ["workshops"]; demo.save()
    else:
        demo.enabled = False; demo.save()
    assert session.pk not in {row["id"] for row in service.session_rows(demo)}
    with pytest.raises((ValidationError, Http404)):
        requests.submit_request(SLUG, session.pk, body, browser_key, for_reservation=True)
    assert not WorkshopRequest.objects.exists() and not PaymentOrder.objects.exists()


def test_public_unpaid_endpoint_cannot_enable_reservation_intake(demo, browser):
    session, data = form_data(browser, demo, count=1)
    session.offering.accepts_requests = False
    session.offering.save()
    target = reverse("scheduling:workshop_request", kwargs={"school_slug": SLUG, "pk": session.pk})
    rejected = browser.post(target, {**data, "for_reservation": "true"})
    assert rejected.status_code == 400 and b"not accepting requests" in rejected.content
    body = QueryDict(mutable=True); body.update(data)
    key = signing.loads(data["request_token"], salt=requests.TOKEN_SALT)["browser"]
    with pytest.raises(ValidationError, match="not accepting requests"):
        requests.submit_request(SLUG, session.pk, body, key)
    assert not WorkshopRequest.objects.exists()


def test_camp_dates_cannot_be_booked_as_single_workshops(demo, browser):
    session = Camp.objects.get(offering__location__profile=demo).sessions.first()
    assert session.pk not in {row["id"] for row in service.session_rows(demo)}
    assert browser.get(url("choose", pk=session.pk)).status_code == 404
    catalog = requests.catalog_for(session.offering)
    body = QueryDict(mutable=True)
    body["request_token"] = requests.request_token(session, catalog, "browser")
    with pytest.raises(ValidationError, match="not accepting requests"):
        requests.submit_request(SLUG, session.pk, body, "browser", for_reservation=True)
    assert not WorkshopRequest.objects.exists()


def held(browser, demo, count=2):
    session, data = form_data(browser, demo, count)
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200
    assert review.context["total"] == 65*count
    assert b"This demo charges nothing" not in review.content
    assert b"Simulate booking" in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    return WorkshopRequest.objects.get(public_id=result.url.rstrip("/").split("/")[-1]), data


def confirmed(browser, demo):
    item, data = held(browser, demo)
    response = browser.post(url("booking", public_id=item.public_id), {"action": "complete"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.reservation.state == "confirmed"
    return item


def test_customer_mock_presentation_preserves_review_and_confirmation_contract(demo, browser):
    from html.parser import HTMLParser

    class Elements(HTMLParser):
        def __init__(self, response):
            super().__init__()
            self.nodes = []
            self.feed(response.content.decode())

        def handle_starttag(self, tag, attrs):
            self.nodes.append((tag, dict(attrs)))

        def find(self, tag=None, **attrs):
            return [attributes for name, attributes in self.nodes if (not tag or name == tag)
                and all(attributes.get(key) == value for key, value in attrs.items())]

    response = browser.get(url("start"))
    start = Elements(response)
    assert len(start.find("li")) == 3
    assert start.find("img", alt="The Plushie Workshop")
    for identifier in ("calendar-month", "previous-month", "next-month", "summary-session"):
        assert start.find(id=identifier)
    assert b"Let's plan your plushie day." in response.content
    session, data = form_data(browser, demo)
    choices = Elements(browser.get(url("choose", pk=session.pk)))
    assert choices.find(id="group-summary-list") and choices.find(id="group-completeness")
    assert choices.find("section", **{"class": "selection-main"})
    assert choices.find("div", **{"class": "plushie-image"})
    review = browser.post(url("choose", pk=session.pk), data)
    review_html = Elements(review)
    assert b"One last look before the fun." in review.content
    assert len(review_html.find("article", **{"class": "review-person"})) == 2
    assert review_html.find("input", name="review_token")[0]["value"] == review.context["review_token"]
    for index in range(2):
        assert review_html.find("input", name=f"participants-{index}-name")[0]["value"] == data[f"participants-{index}-name"]
    assert review_html.find("button", value="edit") and review_html.find("button", value="checkout")
    assert not WorkshopRequest.objects.exists()
    data.update(action="checkout", review_token=review.context["review_token"])
    result = browser.post(url("choose", pk=session.pk), data)
    assert browser.post(result.url, {"action": "complete"}).status_code == 302
    response = browser.get(result.url)
    confirmation = Elements(response)
    assert b"Your plushie day is on the calendar!" in response.content
    assert len(confirmation.find("article", **{"class": "review-person"})) == 2
    assert confirmation.find("img", **{"class": "workshop-photo"})[0]["src"]
    assert b"Confirmation email preview" not in response.content
    assert b"Next: Notion preparation sync" not in response.content


def test_local_cancel_refund_is_simulated_idempotent_and_audited(demo, browser):
    from core.payments.service import totals
    from core.payments.models import Refund
    item = confirmed(browser, demo)
    owner = get_user_model().objects.get(username="plushie_admin")
    browser.force_login(owner)
    target = reverse("scheduling:reservation_cancel", kwargs={"school_slug": SLUG, "public_id": item.reservation.public_id})
    data = {"key": uuid.uuid4(), "resolution": "with_refund", "reason": "Local cancellation rehearsal"}
    assert browser.post(target, data).status_code == 302
    assert browser.post(target, data).status_code == 302
    item.reservation.refresh_from_db()
    assert item.reservation.state == "canceled"
    assert totals(item.reservation.order)["refunded_cents"] == 13000
    assert not Refund.objects.exists() and item.reservation.order.entries.count() == 2
    detail = browser.get(reverse("scheduling:reservation_detail", kwargs={"school_slug": SLUG, "public_id": item.reservation.public_id}))
    assert b"Refund recorded" in detail.content and b"With refund: Local cancellation rehearsal" in detail.content


@pytest.mark.parametrize("mismatch", ["not_demo", "missing_snapshot", "reference", "key", "channel", "amount", "extra_payment", "not_local"])
def test_local_refund_cannot_bypass_real_payment_guards(demo, browser, monkeypatch, mismatch):
    from scheduling.cancellation import cancel
    item = confirmed(browser, demo)
    owner = get_user_model().objects.get(username="plushie_admin")
    receipt = item.reservation.order.entries.get()
    if mismatch == "not_demo":
        demo.school.is_demo = False; demo.school.save()
    elif mismatch == "missing_snapshot":
        item.session_snapshot.pop("experience_demo"); item.save()
    elif mismatch == "not_local":
        monkeypatch.setattr(config, "local_database", lambda: False)
    elif mismatch == "extra_payment":
        LedgerEntry.objects.create(order=receipt.order, school=demo.school, key=uuid.uuid4(),
            environment="test", actor=owner, kind="payment", amount_cents=100, reference="External receipt")
    else:
        field = "amount_cents" if mismatch == "amount" else mismatch
        setattr(receipt, field, {"reference": "External receipt", "key": uuid.uuid4(),
            "channel": "stripe", "amount": 100}[mismatch])
        if mismatch == "channel":
            receipt.actor = None
        receipt.save()
    with pytest.raises(ValidationError, match="outside Pontora"):
        cancel(owner, SLUG, item.reservation.public_id, key=uuid.uuid4(), resolution="with_refund", reason="Refund")
    item.reservation.refresh_from_db()
    assert item.reservation.state == "confirmed" and not item.reservation.order.entries.filter(kind="refund").exists()
    with pytest.raises(ValidationError, match="Only canceled local demo"):
        service.refund_simulated_payment(owner, demo, item.reservation, uuid.uuid4())


def test_catalog_group_renames_keep_customer_pickers_and_receipt(demo, browser):
    from scheduling.models import ChoiceGroup
    item = confirmed(browser, demo)
    bindings = demo.school.feature_flags["booking_demo_inventory"]["groups"]
    ChoiceGroup.objects.filter(pk=bindings["design_group"]).update(name="Choose a plushie")
    ChoiceGroup.objects.filter(pk=bindings["color_group"]).update(name="Choose a color")
    picker = browser.get(url("choose", pk=item.session_id))
    assert picker.context["catalog_data"]["design_group"] == "Choose a plushie"
    assert picker.context["catalog_data"]["options"]["Choose a color"]
    receipt = browser.get(url("booking", public_id=item.public_id))
    assert receipt.context["people"][0]["design"]
    assert receipt.context["people"][0]["color"]
    call_command("seed_booking_experience_demo", SLUG, password="admin")
    assert ChoiceGroup.objects.get(pk=bindings["design_group"]).name == "Choose a plushie"


def test_consumed_materials_leave_preparation_queue(demo, browser):
    from scheduling import inventory
    item = confirmed(browser, demo)
    owner = get_user_model().objects.get(username="plushie_admin")
    inventory.consume(owner, SLUG, item.reservation.public_id)
    browser.force_login(owner)
    response = browser.get(url("preparation"), {"session": item.session_id})
    assert response.status_code == 200
    assert response.context["totals"] == []
    assert b"Materials used" in response.content
    assert b"No outstanding materials." in response.content
    detail = browser.get(reverse("scheduling:reservation_preparation", kwargs={"school_slug": SLUG, "public_id": item.reservation.public_id}))
    assert b"Mark materials used" not in detail.content


def test_completed_receipt_cannot_restart_checkout_and_drops_from_prep(demo, browser):
    from scheduling.completion import complete
    from scheduling.models import StockAdjustment, StockLevel
    item = confirmed(browser, demo)
    owner = get_user_model().objects.get(username="plushie_admin")
    complete(owner, SLUG, item.reservation.public_id)
    entries = list(LedgerEntry.objects.values())
    counts = list(StockLevel.objects.values_list("pk", "on_hand"))
    receipt = url("booking", public_id=item.public_id)
    assert b"Workshop completed" in browser.get(receipt).content
    assert b"Simulate successful checkout" not in browser.get(receipt).content
    assert browser.post(receipt, {"action": "complete"}).status_code == 302
    assert browser.post(receipt, {"action": "abandon"}).status_code == 400
    assert browser.post(receipt, {"action": "decline"}).status_code == 400
    item.reservation.refresh_from_db(); assert item.reservation.state == "completed"
    assert list(LedgerEntry.objects.values()) == entries
    assert list(StockLevel.objects.values_list("pk", "on_hand")) == counts
    browser.force_login(owner)
    response = browser.get(url("preparation"), {"session": item.session_id})
    assert response.context["rows"] == [] and response.context["totals"] == []


def test_prepared_booking_updates_roster_without_consuming_stock(demo, browser):
    from scheduling import preparation
    from scheduling.models import StockLevel, StockAdjustment
    item = confirmed(browser, demo)
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    counts = list(StockLevel.objects.values_list("pk", "on_hand"))
    adjustments = list(StockAdjustment.objects.values())
    target = reverse("scheduling:preparation_update", kwargs={"school_slug": SLUG, "public_id": item.reservation.public_id})
    for action, expected in [("prepare", 1), ("reopen", 0)]:
        reservation = Reservation.objects.get(pk=item.reservation.pk)
        response = browser.post(target, {"action": action, "return_to": "preparation",
            "edit_revision": preparation.form_for(reservation, demo)["edit_revision"].value()}, follow=True)
        assert response.status_code == 200
        assert response.context["prepared_count"] == expected
        assert len(response.context["totals"]) == (0 if expected else 4)
        assert response.context["selected"].pk == item.session_id
    assert list(StockLevel.objects.values_list("pk", "on_hand")) == counts
    assert list(StockAdjustment.objects.values()) == adjustments
    detail = browser.get(reverse("scheduling:reservation_preparation", kwargs={"school_slug": SLUG, "public_id": item.reservation.public_id}))
    assert b"Mark workshop completed" in detail.content and b"Mark materials used" not in detail.content


def test_pausing_intake_keeps_existing_checkout_receipt_and_staff_views(demo, browser):
    item, data = held(browser, demo)
    demo.native_reservations_enabled = False
    demo.save()
    for target in [url("start"), url("choose", pk=item.session_id)]:
        response = browser.get(target)
        assert response.status_code == 200
        assert b"New bookings are temporarily paused" in response.content
        assert b'id="start-booking"' not in response.content
    assert browser.post(url("choose", pk=item.session_id), data).status_code == 409
    with pytest.raises(Http404):
        service.begin(demo, item.session, QueryDict(), item.browser_digest, config.configuration(SLUG))
    assert browser.post(url("booking", public_id=item.public_id), {"action": "complete"}).status_code == 302
    assert b"Booking confirmed" in browser.get(url("booking", public_id=item.public_id)).content
    assert browser.get(url("calendar", public_id=item.public_id)).status_code == 200
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    assert browser.get(url("preparation")).context["selected"].pk == item.session_id
    assert browser.get(url("integration", public_id=item.public_id)).status_code == 200
    assert Reservation.objects.count() == LedgerEntry.objects.count() == 1


def test_staff_workshop_reservations_and_missing_choices_are_visible(demo, browser):
    session, data = form_data(browser, demo)
    body = QueryDict(mutable=True); body.update(data)
    body = service.payload(body, requests.catalog_for(session.offering))
    browser_key = signing.loads(data["request_token"], salt=requests.TOKEN_SALT)["browser"]
    item, _, _ = requests.submit_request(SLUG, session.pk, body, browser_key)
    owner = get_user_model().objects.get(username="plushie_admin")
    from scheduling import reservations
    booking = reservations.create_reservation(owner, SLUG, "workshop", item.public_id, total_cents=13000, deposit_cents=0)
    reservations.settle_offline(owner, SLUG, booking.public_id, key=uuid.uuid4(), kind="payment", amount_cents=13000, reference="Local staff booking")
    assert "experience_demo" not in item.session_snapshot
    browser.force_login(owner)
    response = browser.get(url("preparation"))
    assert response.context["selected"] == session
    assert len(response.context["rows"]) == 2
    assert response.context["missing_count"] == 0
    assert browser.get(url("integration", public_id=item.public_id)).status_code == 200
    # Historical incomplete data must be called out, not counted as ready materials.
    participant = item.participants.first()
    participant.selections = []; participant.save()
    response = browser.get(url("preparation"), {"session": session.pk})
    assert response.context["missing_count"] == 1
    assert b"Needs selections" in response.content
    assert {row["material"]: row["count"] for row in response.context["totals"]}["Polyester stuffing"] == 300


def test_stock_error_is_visible_and_exact_stock_checkout_retry_is_idempotent(demo, browser):
    from scheduling.models import StockLevel
    session, data = form_data(browser, demo, 1)
    StockLevel.objects.filter(location=session.offering.location).update(on_hand=0)
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 400
    assert b'role="alert"' in response.content and b"only 0" in response.content
    assert not WorkshopRequest.objects.exists()
    for unit, amount in [("m", ".5"), ("g", 150), ("pieces", 2)]:
        StockLevel.objects.filter(location=session.offering.location, material__unit=unit).update(on_hand=amount)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    first = browser.post(url("choose", pk=session.pk), data)
    second = browser.post(url("choose", pk=session.pk), data)
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1


def test_choice_labels_and_order_do_not_change_image_identity(demo, browser):
    from scheduling.models import ChoiceOption
    session, _ = form_data(browser, demo, 1)
    group_pk = demo.school.feature_flags["booking_demo_inventory"]["groups"]["design_group"]
    option = ChoiceOption.objects.get(group_id=group_pk, label="Cat")
    option.label = "Kitten"; option.position = 50; option.save()
    response = browser.get(url("choose", pk=session.pk))
    preview = next(row for row in response.context["catalog_data"]["designs"] if row["name"] == "Kitten")
    assert (preview["x"], preview["y"]) == ("50%", "0%")
    fields = response.context["participants"][0].fields
    assert fields[f"choice_{group_pk}"].choices[0] == ("", "Choose design")
    option.image_url = "/static/demo/plushumi/assets/workshop.jpg"; option.save()
    response = browser.get(url("choose", pk=session.pk))
    preview = next(row for row in response.context["catalog_data"]["designs"] if row["name"] == "Kitten")
    assert preview["image_url"] == option.image_url


def test_legacy_preview_binding_backfill_preserves_staff_edits(demo):
    binding = demo.school.feature_flags["booking_demo_inventory"]
    del binding["design_previews"]
    demo.school.save()
    from scheduling.experience_demo.inventory_setup import bind_previews
    from scheduling.models import InventoryCatalog
    catalog = InventoryCatalog.objects.get(pk=binding["catalog"])
    bind_previews(demo, catalog, config.configuration(SLUG))
    assert len(demo.school.feature_flags["booking_demo_inventory"]["design_previews"]) == 9
    saved = demo.school.feature_flags.copy()
    bind_previews(demo, catalog, config.configuration(SLUG))
    assert demo.school.feature_flags == saved


def test_complete_customer_staff_and_projection_journey(demo, browser):
    start = browser.get(url("start"))
    assert start.status_code == 200 and len(start.context["sessions"]) == 45
    assert b"Wix &amp; Notion not connected" not in start.content
    assert b'class="demo-bar"' not in start.content
    assert b"Local working demo" not in start.content
    assert b"No real money is collected" not in start.content
    item = confirmed(browser, demo)
    receipt = browser.get(url("booking", public_id=item.public_id))
    assert b"Booking confirmed" in receipt.content
    assert b"Demo booking confirmed" not in receipt.content
    assert b"Confirmation email preview" not in receipt.content
    assert b"Saved locally after simulated checkout" not in receipt.content
    assert receipt["Referrer-Policy"] == "same-origin" and "no-store" in receipt["Cache-Control"]
    calendar = browser.get(url("calendar", public_id=item.public_id))
    assert calendar.status_code == 200 and b"BEGIN:VCALENDAR" in calendar.content
    assert b"Not a real reservation" in calendar.content
    assert browser.get(url("preparation")).status_code == 302
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    prep = browser.get(url("preparation"), {"session": item.session_id})
    assert prep.status_code == 200 and len(prep.context["rows"]) == 2
    assert len(prep.context["totals"]) == 4
    assert b'aria-label="Participant preparation" tabindex="0"' in prep.content
    assert b'class="prep-session-label"' in prep.content
    assert item.session.offering.location.name.encode() in prep.content
    assert browser.get(url("preparation")).context["selected"].pk == item.session_id
    projected = browser.get(url("integration", public_id=item.public_id))
    assert projected.status_code == 200
    props = projected.context["projection"].payload["properties"]
    assert item.session_snapshot["location"] in props["Workshop"]["title"][0]["text"]["content"]
    assert "Participant 1: Design: Unicorn; Color: White; Age: 7" in props["Participant selections"]["rich_text"][0]["text"]["content"]
    workspace = browser.get(reverse("scheduling:workspace", kwargs={"school_slug": SLUG}))
    assert b">Reservations</a>" in workspace.content and b"Enrollment admin" not in workspace.content
    assert "Workshop Prep" not in [tab["label"] for tab in prep.context["booking_navigation"]["section"]["tabs"]]
    assert not SchoolAdminMembership.objects.filter(user__username="plushie_admin").exists()


def test_checkout_retries_share_booking_order_and_ledger(demo, browser):
    item, data = held(browser, demo)
    assert browser.get(url("calendar", public_id=item.public_id)).status_code == 404
    for _ in range(2):
        assert browser.post(url("choose", pk=item.session_id), data).status_code == 302
        assert browser.post(url("booking", public_id=item.public_id), {"action": "complete"}).status_code == 302
    assert WorkshopRequest.objects.count() == Reservation.objects.count() == PaymentOrder.objects.count() == LedgerEntry.objects.count() == 1
    assert LedgerEntry.objects.get().amount_cents == 13000


def test_failure_then_cancel_never_appear_in_prep(demo, browser):
    item, _ = held(browser, demo)
    response = browser.post(url("booking", public_id=item.public_id), {"action": "decline"})
    assert b"Simulated payment declined" in response.content
    assert not LedgerEntry.objects.exists()
    assert not service.roster(demo, config.configuration(SLUG), item.session_id)["rows"]
    assert browser.post(url("booking", public_id=item.public_id), {"action": "abandon"}).status_code == 302
    assert b"Checkout canceled" in browser.get(url("booking", public_id=item.public_id)).content
    assert browser.post(url("booking", public_id=item.public_id), {"action": "complete"}).status_code == 400
    assert not LedgerEntry.objects.exists()


def test_confirmed_cannot_be_abandoned_and_invalid_action_rejected(demo, browser):
    item = confirmed(browser, demo)
    for action in ("abandon", "unknown", "decline"):
        assert browser.post(url("booking", public_id=item.public_id), {"action": action}).status_code == 400
    assert LedgerEntry.objects.count() == 1


@pytest.mark.parametrize("change", ["missing", "forged", "different_price", "changed_data", "different_browser", "expired"])
def test_signed_review_required_and_immutable(demo, browser, change, monkeypatch):
    session, data = form_data(browser, demo)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    if change == "missing":
        del data["review_token"]
    elif change == "forged":
        data["review_token"] = "forged"
    elif change == "changed_data":
        data["participants-0-name"] = "Replaced"
    elif change == "different_price":
        original = config.configuration(SLUG)
        monkeypatch.setattr("scheduling.experience_demo.views.context", lambda request, slug: (demo, {**original, "price_cents": 1}))
    elif change == "different_browser":
        browser = Client(HTTP_HOST="localhost")
    elif change == "expired":
        original = signing.TimestampSigner.timestamp
        monkeypatch.setattr(signing.TimestampSigner, "timestamp", lambda signer: signing.b62_encode(int(timezone.now().timestamp())-901))
        payload = signing.loads(data["review_token"], salt=service.REVIEW_SALT)
        data["review_token"] = signing.dumps(payload, salt=service.REVIEW_SALT)
        monkeypatch.setattr(signing.TimestampSigner, "timestamp", original)
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 400 and not WorkshopRequest.objects.exists()


def test_edit_invalid_fields_and_quantity_are_nonmutating(demo, browser):
    session, data = form_data(browser, demo)
    data["action"] = "edit"
    assert browser.post(url("choose", pk=session.pk), data).status_code == 200
    for field, value in [("policy_ack", ""), ("purchaser_email", "bad"), ("participants-TOTAL_FORMS", "100"),
                         ("participants-0-name", ""), ("action", "unknown")]:
        response = browser.post(url("choose", pk=session.pk), {**data, "action": "review", field: value})
        assert response.status_code == 400
    assert not WorkshopRequest.objects.exists()


def test_capacity_rechecked_after_review_and_before_payment(demo, browser):
    item, data = held(browser, demo)
    Session.objects.filter(pk=item.session_id).update(capacity=1)
    assert browser.post(url("booking", public_id=item.public_id), {"action": "complete"}).status_code == 400
    assert not LedgerEntry.objects.exists()
    assert browser.post(url("choose", pk=item.session_id), data).status_code == 400


def test_hold_expiry_and_another_booking_cannot_oversell(demo, browser):
    item, _ = held(browser, demo, 8)
    Reservation.objects.filter(pk=item.reservation.pk).update(hold_until=timezone.now()-timedelta(minutes=1))
    other = Client(HTTP_HOST="localhost")
    second, _ = held(other, demo, 8)
    assert other.post(url("booking", public_id=second.public_id), {"action": "complete"}).status_code == 302
    assert browser.post(url("booking", public_id=item.public_id), {"action": "complete"}).status_code == 400
    assert LedgerEntry.objects.count() == 1


def test_browser_staff_and_tenant_privacy(demo, browser):
    item = confirmed(browser, demo)
    stranger = Client(HTTP_HOST="localhost")
    for name in ("booking", "calendar"):
        assert stranger.get(url(name, public_id=item.public_id)).status_code == 404
    outsider = get_user_model().objects.create_user("outsider")
    stranger.force_login(outsider)
    assert stranger.get(url("preparation")).status_code == 404
    assert stranger.get(url("integration", public_id=item.public_id)).status_code == 404
    other = School.objects.create(slug="other")
    other_profile = SchedulingProfile.objects.create(school=other)
    with pytest.raises(Http404):
        service.customer_booking(other_profile, item.public_id, item.browser_digest)
    with pytest.raises(Http404):
        service.customer_booking(demo, item.public_id, "incorrect")


@pytest.mark.parametrize("guard", ["debug", "database", "host", "http_host", "disabled", "flag", "demo", "capability"])
def test_preview_gates_fail_closed(demo, browser, guard, settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "app.mypontora.com"]
    if guard == "debug": config.settings.DEBUG = False
    elif guard == "database": config.settings.DATABASES["default"]["NAME"] = "prod"
    elif guard == "host": config.settings.DATABASES["default"]["HOST"] = "prod"
    elif guard == "http_host": browser = Client(HTTP_HOST="app.mypontora.com")
    elif guard == "disabled": demo.enabled = False; demo.save()
    elif guard == "flag": demo.school.feature_flags = {}; demo.school.save()
    elif guard == "demo": demo.school.is_demo = False; demo.school.save()
    else: demo.capabilities = []; demo.save()
    assert browser.get(url("start")).status_code == 404


def test_preparation_scope_bad_session_and_empty_state(demo, browser):
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    assert browser.get(url("preparation"), {"session": "oops"}).status_code == 404
    assert browser.get(url("preparation"), {"session": "99999999"}).status_code == 404
    assert b"No confirmed workshop bookings" in browser.get(url("preparation")).content
    Session.objects.all().update(starts_at=timezone.now()-timedelta(days=3), ends_at=timezone.now()-timedelta(days=2))
    assert browser.get(url("preparation")).status_code == 200
    assert browser.get(url("start")).context["sessions"] == []
    assert browser.get(url("choose", pk=Session.objects.first().pk)).status_code == 404


def test_seed_idempotent_scoped_and_safe(demo):
    other = School.objects.create(slug="south-bay-music", feature_flags={"keep": True})
    counts = [Session.objects.count(), Location.objects.count(), Camp.objects.count()]
    call_command("seed_booking_experience_demo", SLUG, password="admin")
    assert counts == [Session.objects.count(), Location.objects.count(), Camp.objects.count()]
    assert get_user_model().objects.get(username="plushie_admin").check_password("admin")
    assert not SchedulingProfile.objects.filter(school=other).exists()
    assert School.objects.get(pk=other.pk).feature_flags == {"keep": True}


def test_seed_refuses_remote(local):
    config.settings.DEBUG = False
    with pytest.raises(CommandError, match="isolated"):
        call_command("seed_booking_experience_demo", SLUG, password="admin")


@pytest.mark.parametrize("field,value", [("is_demo", False), ("stripe_customer_id", "real"), ("smtp_host", "real"), ("app_fee_stripe_secret_key", "real")])
def test_seed_refuses_connected_school(local, field, value):
    School.objects.create(slug=SLUG, **{**{"is_demo": True}, field: value})
    with pytest.raises(CommandError, match="customer"):
        call_command("seed_booking_experience_demo", SLUG, password="admin")


@pytest.mark.parametrize("kind", ["operator", "enrollment", "shared"])
def test_seed_never_changes_shared_password(local, kind):
    user = get_user_model().objects.create_user("plushie_admin", password="keep", is_superuser=kind == "operator")
    school = School.objects.create(slug="other")
    if kind == "enrollment": SchoolAdminMembership.objects.create(user=user, school=school)
    if kind == "shared": SchedulingMembership.objects.create(user=user, profile=SchedulingProfile.objects.create(school=school))
    with pytest.raises(CommandError, match="account"):
        call_command("seed_booking_experience_demo", SLUG, password="admin")
    user.refresh_from_db()
    assert user.check_password("keep") and not School.objects.filter(slug=SLUG).exists()


def test_configuration_no_path_traversal(local):
    for slug in ("../schools/other", "missing"):
        with pytest.raises(Http404): config.configuration(slug)


def test_calendar_escapes_and_folds(demo, browser):
    item = confirmed(browser, demo)
    item.session_snapshot["workshop"] = "\\Comma, semi;\r\n" + "音"*90
    value = service.calendar(item)
    assert "\\\\Comma\\, semi\\;\\n" in value
    assert max(len(line.encode()) for line in value.split("\r\n")) <= 75


def test_post_requires_csrf(demo):
    strict = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
    assert strict.post(url("choose", pk=service.sessions(demo).first().pk), {}).status_code == 403
    session, data = form_data(strict, demo)
    data["csrfmiddlewaretoken"] = strict.cookies["csrftoken"].value
    assert strict.post(url("choose", pk=session.pk), data, HTTP_ORIGIN="http://localhost").status_code == 200


def test_projection_validation_is_reported_not_a_server_error(demo, browser):
    item = confirmed(browser, demo)
    item.purchaser_email = "a"*190 + "@example.invalid"
    item.save()
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    response = browser.get(url("integration", public_id=item.public_id))
    assert response.status_code == 400 and b"invalid time range or email" in response.content


def test_review_edit_does_not_readd_removed_participant(demo, browser):
    session, data = form_data(browser, demo, 1)
    response = browser.post(url("choose", pk=session.pk)+"?participants=2", {**data, "action": "edit"})
    assert response.status_code == 200
    assert response.context["catalog_data"]["initial_count"] == "1"
    assert len(response.context["participants"]) == 1


def test_service_defensive_validation_and_snapshot_fallbacks(demo, browser, monkeypatch):
    item, data = held(browser, demo)
    settings = config.configuration(SLUG)
    person = item.participants.first()
    person.selections = []
    person.save()
    assert service.people(item, settings)[0]["photo"] is None
    assert service.people(item, settings)[0]["swatch"] == "#ffffff"
    demo.enabled = False; demo.save()
    with pytest.raises(Http404): service.begin(demo, item.session, QueryDict(), "browser", settings)
    with pytest.raises(Http404): service.simulate(demo, item, settings, "complete")


def test_viewer_cannot_open_mapping(demo, browser):
    item = confirmed(browser, demo)
    member = SchedulingMembership.objects.get(profile=demo)
    member.role = "viewer"; member.save()
    browser.force_login(member.user)
    assert browser.get(url("preparation")).status_code == 200
    assert browser.get(url("integration", public_id=item.public_id)).status_code == 404


def test_preview_refuses_bypassing_review_policy_and_handles_invalid_request(demo, browser, monkeypatch):
    session, raw = form_data(browser, demo)
    data = QueryDict(mutable=True); data.update(raw)
    catalog = requests.catalog_for(session.offering)
    identity = signing.loads(raw["request_token"], salt=requests.TOKEN_SALT)["browser"]
    settings = config.configuration(SLUG)
    data.pop("policy_ack")
    data["review_token"] = service.review_token(session, data, catalog, identity, settings)
    with pytest.raises(ValidationError, match="policies"):
        service.begin(demo, session, data, identity, settings)
    data["policy_ack"] = "on"
    data["review_token"] = service.review_token(session, data, catalog, identity, settings)
    monkeypatch.setattr(requests, "submit_request", lambda *args, **kwargs: (None, None, None))
    with pytest.raises(ValidationError, match="incomplete"):
        service.begin(demo, session, data, identity, settings)
    assert not WorkshopRequest.objects.exists()


def test_prepare_only_links_granted_integrations(demo, browser):
    confirmed(browser, demo)
    demo.capabilities.remove("integrations"); demo.save()
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    assert b"Preview Notion mapping</a>" not in browser.get(url("preparation")).content
