from datetime import timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from pytest_django.asserts import assertContains
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.http import Http404, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from scheduling import inventory, reservations, requests, camps, parties
from scheduling.models import (SchedulingProfile, SchedulingMembership, Location, Room, Offering, Session,
    ChoiceGroup, ChoiceOption, InventoryCatalog, CatalogVariant, StockLevel, StockAllocation, StockAdjustment,
    WorkshopRequest, RequestParticipant, PartyRequest, Reservation, Camp, Material, MaterialRequirement)
from scheduling.inventory_forms import party_material_forms, material_snapshot
from core.models import School
from core.payments.models import PaymentOrder, PaymentAttempt

pytestmark = pytest.mark.django_db


@pytest.fixture
def stock():
    school = School.objects.create(slug="inventory-demo")
    profile = SchedulingProfile.objects.create(school=school, enabled=True, native_reservations_enabled=True,
        capabilities=["workshops", "parties", "camps", "reservations", "integrations"])
    user = get_user_model().objects.create_user("stock-owner")
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    location = Location.objects.create(profile=profile, name="West studio")
    room = Room.objects.create(location=location, name="Room", capacity=100)
    catalog = InventoryCatalog.objects.create(profile=profile, name="Materials")
    design = ChoiceGroup.objects.create(catalog=catalog, name="Design", tracks_stock=True)
    color = ChoiceGroup.objects.create(catalog=catalog, name="Color", tracks_stock=True)
    cat = ChoiceOption.objects.create(group=design, label="Cat")
    blue = ChoiceOption.objects.create(group=color, label="Blue", swatch="#123456")
    item = CatalogVariant(catalog=catalog, name="Blue cat", option_ids=[cat.pk, blue.pk]); item.full_clean(); item.save()
    material = Material.objects.create(profile=profile, name="Blue cat fabric panels", sku="FAB-01", unit="pieces")
    MaterialRequirement.objects.create(variant=item, material=material, quantity=1)
    level = StockLevel.objects.create(material=material, location=location, on_hand=5)
    offering = Offering.objects.create(location=location, name="Workshop", inventory_catalog=catalog,
        is_published=True, accepts_requests=True, accepts_party_requests=True)
    start = timezone.now()+timedelta(days=30)
    session = Session.objects.create(offering=offering, room=room, starts_at=start, ends_at=start+timedelta(hours=1),
        capacity=100, is_published=True)
    client = Client(); client.force_login(user)
    return SimpleNamespace(**locals())


def url(s, name, **kwargs):
    return reverse("scheduling:"+name, kwargs={"school_slug": s.school.slug, **kwargs})


def selections(s):
    return [{"group_id": s.design.pk, "group": "Design", "option_id": s.cat.pk, "option": "Cat"},
            {"group_id": s.color.pk, "group": "Color", "option_id": s.blue.pk, "option": "Blue"}]


def request(s, quantity=1):
    source = WorkshopRequest.objects.create(session=s.session, purchaser_name="Parent", purchaser_email="p@example.invalid",
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b", session_snapshot={"workshop": "Workshop", "timezone": "UTC"})
    for index in range(quantity):
        RequestParticipant.objects.create(request=source, name="Child", position=index, selections=selections(s))
    return source


def reserve(s, quantity=1, total=100):
    source = request(s, quantity)
    return reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id,
        total_cents=total, deposit_cents=0)


def confirm(s, item):
    return reservations.settle_offline(s.user, s.school.slug, item.public_id, key=uuid.uuid4(),
        kind="payment", amount_cents=item.order.total_cents, reference=f"Local test {item.public_id}")


def test_hold_confirm_cancel_and_consumption(stock):
    s = stock
    booking = reserve(s, 2)
    assert inventory.available(s.level) == 3
    confirm(s, booking)
    inventory.consume(s.user, s.school.slug, booking.public_id)
    inventory.consume(s.user, s.school.slug, booking.public_id)
    s.level.refresh_from_db()
    assert s.level.on_hand == 3 and inventory.reserved(s.level) == 0
    assert StockAdjustment.objects.count() == 1
    reservations.cancel_reservation(s.user, s.school.slug, booking.public_id)
    s.level.refresh_from_db()
    assert s.level.on_hand == 3
    other = reserve(s, 2)
    reservations.cancel_reservation(s.user, s.school.slug, other.public_id)
    assert inventory.available(s.level) == 3


def test_expired_hold_rechecks_and_late_money_needs_review(stock):
    s = stock
    first = reserve(s, 5)
    with pytest.raises(ValidationError, match="only 0"):
        reserve(s)
    assert PaymentOrder.objects.count() == 1
    first.hold_until = timezone.now()-timedelta(seconds=1); first.save()
    second = reserve(s, 5); confirm(s, second)
    with pytest.raises(ValidationError, match="enough stock"):
        inventory.ensure_stock(first)
    first = confirm(s, first)
    assert first.state == "review"
    assert inventory.available(s.level) == 0


def test_pending_online_checkout_keeps_stock_even_when_local_hold_expires(stock):
    from core.payments.models import PaymentAccount
    s = stock
    booking = reserve(s, 5)
    account = PaymentAccount.objects.create(school=s.school, environment="test", reference="acct_local", enabled=True)
    # The payment attempt schema is populated through the shared checkout service.
    booking.order.account = account; booking.order.save()
    from core.payments import checkout
    from unittest.mock import patch
    with patch("core.payments.checkout.validate_account"):
        checkout.prepare_attempt(booking.order, return_url="http://localhost/return/")
    booking.hold_until = timezone.now()-timedelta(hours=1); booking.save()
    assert inventory.available(s.level) == 0


def test_stock_adjustments_validate_and_are_audited(stock):
    s = stock
    reserve(s, 2)
    for args in [dict(on_hand=1, previous=5, reason="Count"), dict(on_hand=6, previous=4, reason="Count"),
                 dict(on_hand=-1, previous=5, reason="Count"), dict(on_hand=8, previous=5, reason=""),
                 dict(on_hand=8, previous=5, reason="x"*251)]:
        with pytest.raises(ValidationError): inventory.adjust(s.user, s.school.slug, s.level.pk, **args)
    inventory.adjust(s.user, s.school.slug, s.level.pk, on_hand=7, previous=5, reason="Delivery")
    inventory.adjust(s.user, s.school.slug, s.level.pk, on_hand=7, previous=7, reason="No change")
    assert StockAdjustment.objects.get().delta == 2
    assert inventory.stock_rows(s.profile)[0]["available"] == 5


def test_catalog_cannot_be_switched_after_requests(stock):
    s = stock
    request(s)
    s.offering.inventory_catalog = None
    with pytest.raises(ValidationError, match="already has requests"):
        s.offering.full_clean()


def test_stock_search_pagination_and_filter_isolation(stock):
    s = stock
    for index in range(30):
        material = Material.objects.create(profile=s.profile, name=f"Material {index:02}", sku=f"MAT-{index}")
        StockLevel.objects.create(material=material, location=s.location, on_hand=index)
    first = s.client.get(url(s, "settings"))
    assert len(first.context["stock"]) == 25
    assertContains(first, 'Materials Inventory')
    second = s.client.get(url(s, "settings"), {"page": 2})
    assert len(second.context["stock"]) == 6
    search = s.client.get(url(s, "settings"), {"q": "blue CAT", "location": s.location.pk})
    assert len(search.context["stock"]) == 1
    assert search.context["stock"][0]["level"] == s.level
    assertContains(search, f'<a href="?location={s.location.pk}">Clear</a>', html=True)
    empty = s.client.get(url(s, "settings"), {"q": "no match"})
    assert len(empty.context["stock"]) == 0
    assertContains(empty, 'No materials match this view.')


def test_catalog_summary_counts_shared_items_once_and_shows_unassigned_catalog(stock):
    s = stock
    Offering.objects.create(location=s.location, name="Camp", inventory_catalog=s.catalog)
    InventoryCatalog.objects.create(profile=s.profile, name="Unassigned materials")
    other_school = School.objects.create(slug="other-inventory")
    other_profile = SchedulingProfile.objects.create(school=other_school, enabled=True)
    InventoryCatalog.objects.create(profile=other_profile, name="Private catalog")
    response = s.client.get(url(s, "catalogs"))
    catalogs = {catalog.name: catalog.items.count() for catalog in response.context["catalogs"]}
    assert catalogs == {"Materials": 1, "Unassigned materials": 0}
    assertContains(response, "2 assigned workshops")
    assertContains(response, "0 assigned workshops")
    assert b"Private catalog" not in response.content


@pytest.mark.parametrize("role", ["owner", "viewer", "scheduler"])
def test_catalog_option_controls_are_labeled_and_permission_scoped(stock, role):
    s = stock
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    response = s.client.get(url(s, "inventory_catalog", catalog_pk=s.catalog.pk))
    assert response.status_code == 200
    assert b'Colors &amp; other booking choices' not in response.content
    settings = s.client.get(url(s, "catalog_settings", catalog_pk=s.catalog.pk))
    assertContains(settings, 'Catalog settings')
    assertContains(settings, 'Blue')
    assert (b'Edit Blue' in settings.content) == (role == "owner")
    assertContains(response, 'Materials for one participant')
    if role == "owner":
        assertContains(response, 'Edit design')
        assertContains(response, 'Edit recipe')
    else:
        assert b'Edit design' not in response.content


def test_seed_inventory_preserves_renames_and_backfills_historical_selections(stock):
    from scheduling.experience_demo.inventory_setup import seed_inventory
    s = stock
    booking = reserve(s)
    StockAllocation.objects.all().delete()
    config = {"design_group": "Design", "color_group": "Color", "age_group": "Age",
        "designs": [{"name": "Cat"}], "colors": [{"name": "Blue", "hex": "#123456"}]}
    catalog = seed_inventory(s.profile, config, s.user)
    assert booking.stock_allocations.count() == 3
    catalog.name = "Renamed materials"; catalog.save()
    level = StockLevel.objects.get(material__profile=s.profile, material__name="Blue fleece")
    inventory.adjust(s.user, s.school.slug, level.pk, on_hand=15, previous=20, reason="Physical count")
    assert seed_inventory(s.profile, config, s.user) == catalog
    level.refresh_from_db()
    assert level.on_hand == 15
    assert StockAllocation.objects.count() == 3


def test_seed_inventory_handles_legacy_camp_and_party(stock):
    from scheduling.experience_demo.inventory_setup import seed_inventory
    from scheduling.models import CampRegistration
    s = stock
    camp = Camp.objects.create(offering=s.offering, name="Legacy camp", price=10)
    source = CampRegistration.objects.create(camp=camp, purchaser_name="Parent", purchaser_email="p@example.invalid",
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b",
        participants=[{"name": "Child", "selections": selections(s)}])
    booking = reserve(s)
    StockAllocation.objects.all().delete()
    booking.workshop_request = None; booking.session = None; booking.room = None
    booking.camp_registration = source; booking.save()
    party = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="p@example.invalid",
        group_size=2, starts_at=s.session.starts_at, ends_at=s.session.ends_at, space="shared",
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b")
    other = reserve(s)
    other.stock_allocations.all().delete()
    other.workshop_request = None; other.session = None; other.party_request = party; other.save()
    config = {"design_group": "Design", "color_group": "Color", "age_group": "Age",
        "designs": [{"name": "Cat"}], "colors": [{"name": "Blue", "hex": "#123456"}]}
    with pytest.raises(ValidationError, match="party bookings"):
        seed_inventory(s.profile, config, s.user)
    assert not InventoryCatalog.objects.filter(name="Plushie designs").exists()
    s.profile.school.refresh_from_db()
    other.state = "canceled"; other.save()
    source.participants = [{"name": "Other", "selections": []}]; source.save()
    with pytest.raises(ValidationError, match="unmatched material"):
        seed_inventory(s.profile, config, s.user)
    s.profile.school.refresh_from_db()
    source.participants = [{"name": "Child", "selections": selections(s)}]; source.save()
    catalog = seed_inventory(s.profile, config, s.user)
    assert booking.stock_allocations.count() == 3
    assert not other.stock_allocations.exists()


def test_every_settings_tab_has_a_working_destination(stock):
    from html.parser import HTMLParser
    class SettingsLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside, self.links = False, []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "nav":
                self.inside = attrs.get("aria-label") == "Settings sections"
            if self.inside and tag == "a":
                self.links.append(attrs["href"])
        def handle_endtag(self, tag):
            if tag == "nav":
                self.inside = False
    s = stock
    document = SettingsLinks()
    document.feed(s.client.get(url(s, "locations")).content.decode())
    assert document.links == [
        url(s, "locations"), url(s, "access"), url(s, "integrations"),
        reverse("school_billing", args=[s.school.slug]),
    ]
    for link in document.links:
        assert s.client.get(link).status_code == 200


def test_seed_upgrades_existing_catalog_binding_without_reset(stock):
    from scheduling.experience_demo.inventory_setup import seed_inventory
    s = stock
    s.catalog.name = "Plushie designs"; s.catalog.save()
    ChoiceGroup.objects.create(catalog=s.catalog, name="Age")
    catalog = seed_inventory(s.profile, {"design_group": "Design", "color_group": "Color", "age_group": "Age"}, s.user)
    assert catalog == s.catalog
    s.school.refresh_from_db(); s.level.refresh_from_db()
    assert s.school.feature_flags["booking_demo_inventory"]["catalog"] == catalog.pk
    assert s.level.on_hand == 5


def test_studio_isolation_missing_combinations_and_archives(stock):
    s = stock
    second_location = Location.objects.create(profile=s.profile, name="East")
    second = Offering.objects.create(location=second_location, name="Other", inventory_catalog=s.catalog)
    with pytest.raises(ValidationError, match="unavailable"):
        inventory.check_selections(second, [{"selections": selections(s)}])
    with pytest.raises(ValidationError, match="combination"):
        inventory.check_selections(s.offering, [{"selections": []}])
    s.item.is_active = False; s.item.save()
    with pytest.raises(ValidationError): inventory.check_items(s.offering, {s.item.pk: 1})
    s.item.is_active = True; s.item.save()
    s.blue.is_active = False; s.blue.save()
    with pytest.raises(ValidationError): inventory.check_items(s.offering, {s.item.pk: 1})
    with pytest.raises(ValidationError): inventory.check_items(s.offering, {999999: 1})
    s.offering.inventory_catalog = None
    assert inventory.check_selections(s.offering, [{"selections": []}]) == []


def test_catalog_and_model_invariants(stock):
    s = stock
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other"))
    foreign = InventoryCatalog.objects.create(profile=other, name="Private")
    s.offering.inventory_catalog = foreign
    with pytest.raises(ValidationError): s.offering.full_clean()
    loc = Location.objects.create(profile=other, name="Private")
    with pytest.raises(ValidationError): StockLevel(material=s.material, location=loc).full_clean()
    for ids in [[], "bad", [True], [s.cat.pk], [s.cat.pk, s.cat.pk], [s.cat.pk, s.blue.pk, 99999]]:
        with pytest.raises(ValidationError): CatalogVariant(catalog=s.catalog, name="Wrong", option_ids=ids).full_clean()
    new = ChoiceOption.objects.create(group=s.design, label="Dog")
    s.item.option_ids = [new.pk, s.blue.pk]
    with pytest.raises(ValidationError, match="combination cannot change"): s.item.full_clean()
    s.design.tracks_stock = False
    with pytest.raises(ValidationError): s.design.full_clean()
    s.design.tracks_stock = True; s.design.is_active = False
    with pytest.raises(ValidationError): s.design.full_clean()
    with pytest.raises(ValidationError): ChoiceGroup(catalog=s.catalog, name="Size", tracks_stock=True).full_clean()
    blank = InventoryCatalog.objects.create(profile=s.profile, name="Empty")
    s.offering.inventory_catalog = blank
    with pytest.raises(ValidationError, match="no design choices"): inventory.selection_items(s.offering, [])


def test_party_and_camp_share_workshop_stock(stock):
    s = stock
    party = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="p@example.invalid",
        starts_at=s.session.starts_at+timedelta(days=3), ends_at=s.session.ends_at+timedelta(days=3), room=s.room,
        group_size=3, space="shared", status="reviewed", proposed_total=100, proposed_deposit=0,
        materials=[{"item_id": s.item.pk, "name": s.item.name, "quantity": 3}], offering_snapshot={"workshop": "Party", "timezone": "UTC"},
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b")
    booked = reservations.create_reservation(s.user, s.school.slug, "party", party.public_id)
    assert inventory.available(s.level) == 2
    with pytest.raises(ValidationError, match="only 2"): reserve(s, 3)
    party2 = PartyRequest.objects.get(pk=party.pk); party2.pk = None; party2.public_id = uuid.uuid4(); party2.submission_key = uuid.uuid4(); party2.materials = []; party2.save()
    with pytest.raises(ValidationError, match="every party participant"):
        reservations.create_reservation(s.user, s.school.slug, "party", party2.public_id)
    second = Session.objects.create(offering=s.offering, room=s.room, starts_at=s.session.starts_at+timedelta(days=1),
        ends_at=s.session.ends_at+timedelta(days=1), capacity=100, is_published=True)
    camp = Camp.objects.create(offering=s.offering, name="Camp", price=10, deposit=0, is_open=True)
    camp.sessions.set([s.session, second])
    catalog = requests.catalog_for(s.offering)
    data = QueryDict(mutable=True); data.update({"request_token": camps.registration_token(camp, camps.camp_dates(camp), catalog, "b"),
        "purchaser_name": "Parent", "purchaser_email": "p@example.invalid", "participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0",
        "participants-0-name": "Child", f"participants-0-choice_{s.design.pk}": str(s.cat.pk), f"participants-0-choice_{s.color.pk}": str(s.blue.pk)})
    registration, _, _ = camps.register_camp(s.school.slug, camp.pk, data, "b")
    assert registration.reservation.stock_allocations.get().quantity == 1
    assert inventory.available(s.level) == 1
    inventory.ensure_stock(registration.reservation)


def test_settings_permissions_and_tenant_isolation(stock):
    s = stock
    assert b"admin/dashboard.css" in s.client.get(url(s, "settings")).content
    assert s.client.get(url(s, "settings"), {"location": s.location.pk}).status_code == 200
    assert s.client.get(url(s, "settings"), {"location": 999999}).status_code == 400
    assert s.client.get(url(s, "inventory_catalog", catalog_pk=s.catalog.pk)).status_code == 200
    for role in ("viewer", "scheduler"):
        SchedulingMembership.objects.filter(user=s.user).update(role=role)
        assert s.client.get(url(s, "settings")).status_code == 200
        for name, kwargs in [("inventory_catalog_new", {}), ("inventory_stock", {"pk": s.level.pk})]:
            assert s.client.get(url(s, name, **kwargs)).status_code == 404
            assert s.client.post(url(s, name, **kwargs), {}).status_code == 404
    SchedulingMembership.objects.filter(user=s.user).update(is_active=False)
    assert s.client.get(url(s, "settings")).status_code == 404


def post_form(client, target, data):
    response = client.get(target)
    form = response.context["form"]
    for field in form.hidden_fields():
        data[field.name] = field.value()
    return client.post(target, data)


def test_catalog_crud_stale_edits_stock_count_and_consume_ui(stock):
    s = stock
    response = s.client.post(url(s, "inventory_catalog_new"), {"name": "Party kits"})
    assert response.status_code == 302
    catalog = InventoryCatalog.objects.get(name="Party kits")
    catalog.choice_groups.all().delete()
    group_url = url(s, "inventory_group_new", catalog_pk=catalog.pk)
    assert s.client.post(group_url, {"name": "Shape", "position": 0, "is_active": "on", "tracks_stock": "on"}).status_code == 302
    group = catalog.choice_groups.get()
    opt_url = url(s, "inventory_option_new", catalog_pk=catalog.pk, group_pk=group.pk)
    assert s.client.post(opt_url, {"label": "Star", "position": 0, "is_active": "on", "image_url": "javascript:bad"}).status_code == 400
    assert s.client.post(opt_url, {"label": "Star", "position": 0, "is_active": "on"}).status_code == 302
    option = group.options.get()
    item_url = url(s, "inventory_item_new", catalog_pk=catalog.pk)
    assert s.client.get(item_url).status_code == 200
    assert s.client.post(item_url, {"name": "Star kit", "is_active": "on", f"group_{group.pk}": option.pk}).status_code == 302
    item = catalog.items.get()
    assert not item.requirements.exists()
    assert s.client.post(item_url, {"name": "Duplicate", "is_active": "on", f"group_{group.pk}": option.pk}).status_code == 400
    edit_url = url(s, "inventory_item_edit", catalog_pk=catalog.pk, pk=item.pk)
    response = post_form(s.client, edit_url, {"name": "Star kit", "is_active": "on"})
    assert response.status_code == 302
    group_edit = url(s, "inventory_group_edit", catalog_pk=catalog.pk, pk=group.pk)
    assert post_form(s.client, group_edit, {"name": "Shape", "position": 1, "is_active": "on", "tracks_stock": "on"}).status_code == 302
    option_edit = url(s, "inventory_option_edit", catalog_pk=catalog.pk, group_pk=group.pk, pk=option.pk)
    assert post_form(s.client, option_edit, {"label": "Bright star", "position": 0, "is_active": "on", "swatch": "#112233"}).status_code == 302
    rename_url = url(s, "inventory_catalog_edit", catalog_pk=catalog.pk)
    token = s.client.get(rename_url).context["form"]["edit_revision"].value()
    catalog.name = "Renamed elsewhere"; catalog.save()
    assert s.client.post(rename_url, {"name": "Clobber", "edit_revision": token}).status_code == 400
    count_url = url(s, "inventory_stock", pk=s.level.pk)
    count_result = s.client.post(count_url, {"mode": "receive", "on_hand": 3, "previous": 5, "reason": "Delivery", "next": "https://untrusted.invalid/"})
    assert count_result.status_code == 302
    assert count_result.url == f'{url(s, "settings")}?location={s.location.pk}'
    assert s.client.get(count_result.url).context["selected_location"] == s.location
    count_page = s.client.get(count_url)
    assertContains(count_page, 'Stock activity')
    assertContains(count_page, 'Delivery')
    assert s.client.post(count_url, {"on_hand": 9, "previous": 5, "reason": "Stale"}).status_code == 400
    assert s.client.post(count_url, {"on_hand": "bad"}).status_code == 400
    booking = reserve(s)
    use_url = url(s, "reservation_complete", public_id=booking.public_id)
    assert s.client.get(use_url).status_code == 200
    assert s.client.post(use_url, {}).status_code == 400
    assert s.client.post(use_url, {"action": "complete"}).status_code == 400
    assert not booking.stock_allocations.get().consumed_at
    confirm(s, booking)
    assert s.client.post(use_url, {"action": "complete"}).status_code == 302
    assert booking.stock_allocations.get().consumed_at


def test_party_form_and_review_materials(stock):
    s = stock
    target = url(s, "party_inquiry", pk=s.offering.pk)
    response = s.client.get(target)
    data = {"request_token": response.context["request_token"], "organizer_name": "Party", "organizer_email": "p@example.invalid",
        "starts_at": (timezone.now()+timedelta(days=40)).strftime("%Y-%m-%dT10:00"), "duration_minutes": 60,
        "group_size": 2, "space": "shared", "materials-TOTAL_FORMS": 1, "materials-INITIAL_FORMS": 0,
        "materials-0-design": s.cat.pk, "materials-0-color": s.blue.pk, "materials-0-quantity": 2}
    assert s.client.post(target, {**data, "materials-0-quantity": 1}).status_code == 400
    assert s.client.post(target, {**data, "materials-0-design": 999999}).status_code == 400
    result = s.client.post(target, data)
    assert result.status_code == 302
    party = PartyRequest.objects.get()
    assert party.materials[0]["quantity"] == 2
    review = url(s, "party_detail", public_id=party.public_id)
    assert b"Participant designs &amp; colors" in s.client.get(review).content
    review_data = {"revision": 0, "status": "reviewed", "room": s.room.pk, "proposed_total": 100, "proposed_deposit": 0,
        "materials-TOTAL_FORMS": 1, "materials-INITIAL_FORMS": 1, "materials-0-design": s.cat.pk, "materials-0-color": s.blue.pk, "materials-0-quantity": 2}
    assert s.client.post(review, {**review_data, "materials-0-quantity": 0}).status_code == 400
    assert s.client.post(review, review_data).status_code == 302
    party.refresh_from_db()
    assert party.status == "reviewed"
    assert inventory.available(s.level) == 5  # An inquiry never consumes stock.
    assert material_snapshot(None, 2) == []
    formset = party_material_forms(s.offering, {"materials-TOTAL_FORMS": "0", "materials-INITIAL_FORMS": "0"})
    assert formset.is_valid() and material_snapshot(formset, 2) == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_reservations_cannot_sell_last_item_twice(stock):
    s = stock
    s.level.on_hand = 1; s.level.save()
    sources = [request(s), request(s)]
    def book(source):
        close_old_connections()
        try:
            reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id, total_cents=100, deposit_cents=0)
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(book, sources))
    assert sorted(result) == [False, True]
    assert StockAllocation.objects.count() == PaymentOrder.objects.count() == 1
