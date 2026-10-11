import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404, QueryDict
from django.test import Client
from django.utils import timezone

from core.models import AdminAuditLog, Submission
from core.payments.models import PaymentOrder
from core.payments.service import totals
from scheduling.camp_forms import CampForm, validate_dates
from scheduling.camps import (TOKEN_SALT, camp_dates, camp_digest, camp_form, places_left, register_camp,
                              registration_token, save_camp)
from scheduling.models import (Camp, CampRegistration, ChoiceGroup, ChoiceOption, Offering, Reservation,
                               ReservationOccurrence, Session)
from scheduling.requests import catalog_for
from scheduling.reservations import cancel_reservation, ensure_capacity, session_places_taken
from scheduling.setup import set_publication
from scheduling.tests.test_reservations import inventory, path, party_request, reserve, settle, workshop_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def camp(inventory):
    _, offering, room, first = inventory
    dates = [first] + [Session.objects.create(offering=offering, room=room,
        starts_at=first.starts_at+timedelta(days=i), ends_at=first.ends_at+timedelta(days=i),
        capacity=4, is_published=True) for i in (1, 2)]
    camp = Camp.objects.create(offering=offering, name="Three-day makers camp", price="100.01", deposit="25.01", is_open=True)
    camp.sessions.set(dates)
    group = ChoiceGroup.objects.create(offering=offering, name="Color", reference_image="/static/demo/plushumi/assets/logo.png")
    ChoiceOption.objects.create(group=group, label="Blue")
    return camp


def payload(camp, count=2, browser="browser", token=None):
    data = QueryDict(mutable=True)
    catalog = catalog_for(camp.offering)
    data.update({"request_token": token or registration_token(camp, camp_dates(camp), catalog, browser),
        "purchaser_name": "Camp Parent", "purchaser_email": "camp@example.com",
        "participants-TOTAL_FORMS": str(count), "participants-INITIAL_FORMS": "0"})
    for i in range(count):
        data[f"participants-{i}-name"] = f"Camper {i}"
        for group in catalog:
            if group.active_options:
                data[f"participants-{i}-choice_{group.pk}"] = str(group.active_options[0].pk)
    return data


def register(camp, count=2, data=None, browser="browser"):
    return register_camp(camp.offering.location.profile.school.slug, camp.pk,
        data if data is not None else payload(camp, count, browser), browser)[0]


def setup_data(camp, **changes):
    form = camp_form(camp.offering.location.profile, camp.offering_id, camp.pk)
    data = {"name": camp.name, "description": "Make, learn and create.", "sessions": list(camp.sessions.values_list("pk", flat=True)),
        "price": str(camp.price), "deposit": str(camp.deposit), "is_open": "on",
        "edit_revision": form["edit_revision"].value()}
    data.update(changes)
    return data


def test_one_order_all_dates_and_cent_allocation(camp, booking_member):
    before = list(Submission.objects.values_list("pk", flat=True))
    registration = register(camp)
    item = registration.reservation
    assert item.state == "held" and item.quantity == 2 and item.room_id is None
    assert item.order.total_cents == 20002 and item.order.deposit_cents == 5002
    occurrences = list(item.occurrences.all())
    assert len(occurrences) == 3
    assert [o.total_cents for o in occurrences] == [6668, 6667, 6667]
    assert sum(o.deposit_cents for o in occurrences) == 5002
    assert occurrences[0].total == Decimal("66.68")
    assert all(session_places_taken(s) == 2 for s in camp_dates(camp))
    assert PaymentOrder.objects.count() == 1 and not item.order.entries.exists()
    assert str(camp) == camp.name
    assert registration.participants[0]["selections"][0]["option"] == "Blue"
    assert settle(booking_member, item, 5001).state == "held"
    assert settle(booking_member, item, 1).state == "confirmed"
    assert settle(booking_member, item, 15000).state == "confirmed"
    assert list(Submission.objects.values_list("pk", flat=True)) == before


@pytest.mark.parametrize("blocked_date", [0, 1, 2])
def test_one_full_date_rolls_back_everything(camp, inventory, booking_member, blocked_date):
    session = camp_dates(camp)[blocked_date]
    reserve(booking_member, workshop_request((*inventory[:3], session), count=3))
    with pytest.raises(ValidationError, match="every date"):
        register(camp)
    assert not CampRegistration.objects.exists() and not ReservationOccurrence.objects.exists()
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1
    assert places_left(camp_dates(camp)) == 1


def test_individual_workshop_and_other_camp_share_seats(camp, inventory, booking_member):
    register(camp, 3)
    with pytest.raises(ValidationError, match="whole group"):
        reserve(booking_member, workshop_request(inventory))
    second = Camp.objects.create(offering=camp.offering, name="Other cohort", price=0, is_open=True)
    second.sessions.set(camp.sessions.all())
    with pytest.raises(ValidationError):
        register(second)
    assert register(second, 1).reservation.state == "confirmed"
    assert places_left(camp_dates(camp)) == 0


def test_cancel_releases_every_date_but_never_refunds_silently(camp, booking_member):
    item = register(camp, 4).reservation
    settle(booking_member, item, 10004)
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, item.public_id)
    assert places_left(camp_dates(camp)) == 4 and item.occurrences.count() == 3
    assert totals(item.order)["net_cents"] == 10004
    settle(booking_member, item, 10004, kind="refund")
    assert totals(item.order)["net_cents"] == 0


def test_expired_camp_late_payment_never_partially_confirms(camp, inventory, booking_member):
    item = register(camp, 3).reservation
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(seconds=1))
    assert places_left(camp_dates(camp)) == 4
    middle = camp_dates(camp)[1]
    reserve(booking_member, workshop_request((*inventory[:3], middle), count=2))
    assert settle(booking_member, item, 7503).state == "review"
    assert [session_places_taken(s) for s in camp_dates(camp)] == [0, 2, 0]
    assert totals(item.order)["net_cents"] == 7503


def test_expired_camp_can_reacquire_all_dates(camp, booking_member):
    item = register(camp).reservation
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(seconds=1))
    assert settle(booking_member, item, 5002).state == "confirmed"
    assert places_left(camp_dates(camp)) == 2


def test_camp_is_not_a_continuous_room_reservation(camp, inventory, booking_member):
    register(camp)
    dates = camp_dates(camp)
    assert reserve(booking_member, party_request(inventory, start=dates[0].ends_at)).pk
    with pytest.raises(ValidationError):
        reserve(booking_member, party_request(inventory, start=dates[1].starts_at))
    Session.objects.filter(pk=dates[1].pk).update(is_published=False)
    with pytest.raises(ValidationError, match="camp reservation"):
        reserve(booking_member, party_request(inventory, start=dates[1].starts_at))


@pytest.mark.parametrize("change", ["time", "room", "capacity", "unpublish"])
def test_session_edit_and_publication_protect_camp(camp, booking_member, change):
    item = register(camp, 3).reservation
    session = camp_dates(camp)[1]
    if change == "time":
        session.starts_at += timedelta(minutes=5)
    elif change == "room":
        session.room = None
    elif change == "capacity":
        session.capacity = 2
    else:
        with pytest.raises(ValidationError, match="remain published"):
            set_publication(booking_member.user, booking_member.profile.school.slug, "sessions", session.pk, False)
        return
    with pytest.raises(ValidationError):
        session.full_clean()
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, item.public_id)
    if change != "capacity":
        with pytest.raises(ValidationError):
            session.full_clean()


def test_duplicate_retry_after_close_returns_original(camp):
    data = payload(camp)
    first = register(camp, data=data)
    Camp.objects.filter(pk=camp.pk).update(is_open=False)
    assert register(camp, data=data).pk == first.pk
    data["purchaser_name"] = "Changed"
    with pytest.raises(ValidationError, match="different details"):
        register(camp, data=data)
    assert CampRegistration.objects.count() == PaymentOrder.objects.count() == 1


@pytest.mark.parametrize("mode", ["bad", "expired", "wrong_camp", "wrong_browser", "missing_browser", "bad_key"])
def test_registration_token_binding(camp, mode, monkeypatch):
    data = payload(camp)
    if mode == "bad":
        data["request_token"] = "bad"
    elif mode == "expired":
        monkeypatch.setattr(signing.time, "time", lambda: 999999999999)
    else:
        token = signing.loads(data["request_token"], salt=TOKEN_SALT)
        if mode == "wrong_camp": token["camp"] += 1
        if mode == "wrong_browser": token["browser"] = "other"
        if mode == "bad_key": token["key"] = "invalid"
        data["request_token"] = signing.dumps(token, salt=TOKEN_SALT)
    with pytest.raises(ValidationError, match="expired or belongs"):
        register(camp, data=data, browser="" if mode == "missing_browser" else "browser")
    assert not CampRegistration.objects.exists()


@pytest.mark.parametrize("field,value", [("price", "99.99"), ("description", "Changed"), ("name", "Renamed")])
def test_stale_price_and_catalog_rejected(camp, field, value):
    data = payload(camp)
    Camp.objects.filter(pk=camp.pk).update(**{field: value})
    with pytest.raises(ValidationError, match="changed"):
        register(camp, data=data)
    assert not PaymentOrder.objects.exists()


@pytest.mark.parametrize("change", ["closed", "disabled", "unpublished", "started", "no_options", "no_native", "no_account"])
def test_unavailable_registration(camp, client, booking_member, change):
    data = payload(camp)
    if change == "closed": Camp.objects.filter(pk=camp.pk).update(is_open=False)
    if change == "disabled":
        profile = booking_member.profile
        profile.enabled = False
        profile.save()
    if change == "unpublished": camp.sessions.update(is_published=False)
    if change == "started": camp.sessions.update(starts_at=timezone.now()-timedelta(hours=1))
    if change == "no_options": ChoiceOption.objects.update(is_active=False)
    if change == "no_native":
        profile = booking_member.profile
        profile.native_reservations_enabled = False
        profile.save()
    if change == "no_account": Camp.objects.filter(pk=camp.pk).update(online=True)
    assert client.get(path(booking_member, "camp_register", pk=camp.pk)).status_code == 404
    with pytest.raises((ValidationError, Http404)):
        register(camp, data=data)
    assert not CampRegistration.objects.exists()


@pytest.mark.parametrize("changes", [{"participants-TOTAL_FORMS":"0"}, {"participants-TOTAL_FORMS":"100000"},
    {"participants-0-name":""}, {"purchaser_email":"invalid"}])
def test_invalid_participants_and_contact(camp, changes):
    data = payload(camp)
    data.update(changes)
    result, purchaser, participants = register_camp(camp.offering.location.profile.school.slug, camp.pk, data, "browser")
    assert result is None and (purchaser.errors or participants.errors or participants.non_form_errors())
    assert not PaymentOrder.objects.exists()


def test_invalid_choice_and_free_camp(camp):
    data = payload(camp)
    data[f"participants-0-choice_{camp.offering.choice_groups.get().pk}"] = "99999"
    assert register(camp, data=data) is None
    camp.price = camp.deposit = 0
    camp.save()
    assert register(camp).reservation.state == "confirmed"


def test_audit_failure_rolls_back_registration_order_and_all_occurrences(camp, monkeypatch):
    def fail(**kwargs): raise RuntimeError("Audit unavailable")
    monkeypatch.setattr(AdminAuditLog.objects, "create", fail)
    with pytest.raises(RuntimeError): register(camp)
    assert not CampRegistration.objects.exists() and not PaymentOrder.objects.exists()
    assert not Reservation.objects.exists() and not ReservationOccurrence.objects.exists()


def test_camp_setup_create_edit_close_and_frozen_commercial_fields(camp, booking_member):
    user, slug = booking_member.user, booking_member.profile.school.slug
    data = setup_data(camp)
    form = save_camp(user, slug, camp.offering_id, data)
    assert form.is_valid(), form.errors
    assert form.instance.sessions.count() == 3
    register(camp)
    data.update(price="1", deposit="0", sessions=[camp.sessions.first().pk], is_open="")
    form = save_camp(user, slug, camp.offering_id, data, camp.pk)
    assert form.is_valid(), form.errors
    camp.refresh_from_db()
    assert camp.price == Decimal("100.01") and camp.sessions.count() == 3 and not camp.is_open
    camp.price = 99
    with pytest.raises(ValidationError, match="another camp"): camp.full_clean()


@pytest.mark.parametrize("changes", [{"sessions":[]}, {"price":"-1"}, {"deposit":"200"},
    {"online":"on", "price":"0.30", "deposit":"0"}, {"online":"on", "price":"10", "deposit":"9.90"}])
def test_setup_validation(camp, booking_member, changes):
    form = save_camp(booking_member.user, booking_member.profile.school.slug, camp.offering_id, setup_data(camp, **changes), camp.pk)
    assert not form.is_valid()


def test_invalid_session_sets_and_cross_tenant_choices(camp, booking_member, inventory):
    dates = camp_dates(camp)
    for invalid in ([], dates[:1], dates * 11):
        with pytest.raises(ValidationError): validate_dates(camp, invalid)
    dates[1].starts_at = dates[0].starts_at
    with pytest.raises(ValidationError, match="overlap"): validate_dates(camp, dates)
    dates = camp_dates(camp)
    dates[1].offering_id = 9999
    with pytest.raises(ValidationError, match="belong"): validate_dates(camp, dates)
    other = Offering.objects.create(location=inventory[1].location, name="Other")
    form = CampForm(setup_data(camp), offering=other)
    assert not form.is_valid() and "sessions" in form.errors
    with pytest.raises(Http404): camp_form(booking_member.profile, other.pk, camp.pk)


def test_open_setup_requires_published_future_dates_and_native_flag(camp, booking_member):
    camp.sessions.update(is_published=False)
    form = save_camp(booking_member.user, booking_member.profile.school.slug, camp.offering_id, setup_data(camp), camp.pk)
    assert not form.is_valid()
    assert save_camp(booking_member.user, booking_member.profile.school.slug, camp.offering_id,
        setup_data(camp, is_open=""), camp.pk).is_valid()
    camp.sessions.update(is_published=True)
    booking_member.profile.native_reservations_enabled = False
    booking_member.profile.save()
    assert not save_camp(booking_member.user, booking_member.profile.school.slug, camp.offering_id, setup_data(camp), camp.pk).is_valid()


@pytest.mark.parametrize("change", ["missing", "moved", "unpublished"])
def test_corrupt_or_stale_occurrences_fail_closed(camp, booking_member, change):
    item = register(camp).reservation
    if change == "missing": item.occurrences.first().delete()
    if change == "moved":
        from django.db.models import F
        camp.sessions.update(starts_at=F("starts_at")+timedelta(minutes=1))
    if change == "unpublished": camp.sessions.update(is_published=False)
    assert settle(booking_member, item, 5002).state == "review"
    assert totals(item.order)["net_cents"] == 5002


@pytest.mark.parametrize("target,changes", [("camp", {"deposit": 999}), ("occurrence", {"deposit_cents":999999}),
    ("occurrence", {"ends_at":timezone.now()-timedelta(days=1)}), ("reservation", {"camp_registration":None})])
def test_database_constraints(camp, target, changes):
    item = register(camp).reservation
    obj = {"camp":camp, "reservation":item, "occurrence":item.occurrences.first()}[target]
    with pytest.raises(IntegrityError), transaction.atomic():
        type(obj).objects.filter(pk=obj.pk).update(**changes)


def test_public_browser_flow_receipt_is_scoped_and_prices_are_server_owned(camp, client, booking_member):
    url = path(booking_member, "camp_register", pk=camp.pk)
    page = client.get(url)
    assert page.status_code == 200 and b"100.01" in page.content
    data = payload(camp, token=page.context["request_token"])
    data.update(price="0", total_cents="0")
    response = client.post(url, data)
    assert response.status_code == 302
    item = CampRegistration.objects.get().reservation
    assert item.order.total_cents == 20002
    receipt = client.get(response.url)
    assert receipt.status_code == 200 and b"Camp dates" in receipt.content and b"Camper 0" in receipt.content
    assert Client().get(response.url).status_code == 404
    other = Client()
    other.get(url)
    assert other.get(response.url).status_code == 404
    assert client.get(response.url.replace(booking_member.profile.school.slug, "other-school")).status_code == 404
    assert client.post(url, data).url == response.url


def test_public_validation_and_full_camp_screen(camp, client, booking_member):
    url = path(booking_member, "camp_register", pk=camp.pk)
    page = client.get(url)
    data = payload(camp, token=page.context["request_token"])
    data["purchaser_email"] = "invalid"
    response = client.post(url, data)
    assert response.status_code == 400 and b"valid email" in response.content
    assert client.post(url, {"request_token":"bad"}).status_code == 400
    register(camp, 4)
    response = client.get(url)
    assert b">Full<" in response.content and b"Hold places for all" not in response.content
    assert client.put(url).status_code == 405


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_camp_creation_stays_in_camps_setup(camp, client, booking_member, role):
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    create_url = path(booking_member, "camp_new", offering_pk=camp.offering_id)

    workshop = client.get(path(booking_member, "workshop_detail", pk=camp.offering_id))
    assert workshop.status_code == 200
    assert create_url.encode() not in workshop.content
    assert b"Create camp from sessions" not in workshop.content
    assert b"Participant choices" not in workshop.content and b"Sessions" in workshop.content

    camps = client.get(path(booking_member, "camps"))
    assert camps.status_code == 200
    assert (create_url.encode() in camps.content) is (role == "owner")
    assert (b"Create a camp" in camps.content) is (role == "owner")
    assert client.get(create_url).status_code == (200 if role == "owner" else 404)

    edit = client.get(path(booking_member, "workshop_edit", pk=camp.offering_id))
    assert edit.status_code == (200 if role == "owner" else 404)
    assert create_url.encode() not in edit.content


def test_admin_screens_and_preparation_confirmed_only(camp, client, booking_member):
    client.force_login(booking_member.user)
    for name, kwargs in [("camps",{}), ("camp_detail",{"pk":camp.pk}),
        ("camp_new",{"offering_pk":camp.offering_id}), ("camp_edit",{"offering_pk":camp.offering_id,"pk":camp.pk})]:
        assert client.get(path(booking_member,name,**kwargs)).status_code == 200
    url = path(booking_member,"camp_edit",offering_pk=camp.offering_id,pk=camp.pk)
    assert client.post(url, setup_data(camp, deposit="999")).status_code == 400
    assert client.post(url, setup_data(camp)).status_code == 302
    camp.refresh_from_db()
    registration = register(camp)
    item = registration.reservation
    prep = path(booking_member, "preparation", pk=camp.sessions.first().pk)
    assert b"Confirmed camp participants" not in client.get(prep).content
    settle(booking_member, item, 5002)
    assert b"Confirmed camp participants (2)" in client.get(prep).content
    assert client.get(prep).context["camp_totals"] == [{"group":"Color", "option":"Blue", "count":2}]
    assert b"Camper 0" in client.get(prep).content
    assert b"Camp Parent" in client.get(path(booking_member,"camp_detail",pk=camp.pk)).content
    detail = client.get(path(booking_member,"reservation_detail",public_id=item.public_id))
    assert b"Camp dates" in detail.content
    preparation = client.get(path(booking_member,"reservation_preparation",public_id=item.public_id))
    assert b"Participant selections" in preparation.content and b"Camper 0" in preparation.content
    assert b"Camp dates" in preparation.content
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, item.public_id)
    assert b"Confirmed camp participants" not in client.get(prep).content
    Camp.objects.filter(pk=camp.pk).update(is_open=False)
    page = client.get(path(booking_member,"camp_detail",pk=camp.pk))
    assert b"Camp registration is closed" in page.content and b"Open customer registration" not in page.content


@pytest.mark.parametrize("role", ["viewer", "scheduler"])
def test_permission_and_csrf_boundaries(camp, client, booking_member, role):
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    assert client.get(path(booking_member,"camp_detail",pk=camp.pk)).status_code == 200
    edit = path(booking_member,"camp_edit",offering_pk=camp.offering_id,pk=camp.pk)
    assert client.get(edit).status_code == 404
    assert client.post(edit,setup_data(camp)).status_code == 404
    csrf = Client(enforce_csrf_checks=True)
    assert csrf.post(path(booking_member,"camp_register",pk=camp.pk), payload(camp)).status_code == 403


def test_public_schedule_lists_only_available_camps(camp, client, booking_member):
    url = path(booking_member,"public_schedule")
    link = path(booking_member,"camp_register",pk=camp.pk).encode()
    assert link in client.get(url).content
    camp.sessions.update(is_published=False)
    # Keep another session published so the offering itself remains visible.
    Session.objects.filter(pk=camp.sessions.first().pk).update(is_published=True)
    assert link not in client.get(url).content
    assert places_left([]) == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("competitor", ["camp", "single", "duplicate"])
def test_concurrent_registration_cannot_oversell_or_duplicate(camp, inventory, booking_member, competitor):
    data = payload(camp, count=3)
    second = payload(camp, count=3) if competitor == "camp" else data
    source = workshop_request(inventory, 3) if competitor == "single" else None
    barrier = Barrier(2)
    def attempt(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                if index and source:
                    return reserve(booking_member, source).pk
                return register(camp, data=data if not index else second).pk
            except ValidationError:
                return None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [0,1]))
    assert sum(r is not None for r in results) == (2 if competitor == "duplicate" else 1)
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1
    assert session_places_taken(camp.sessions.first()) == 3
