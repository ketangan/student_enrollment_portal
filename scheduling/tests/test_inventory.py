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
        url(s, "locations"), url(s, "guest_communication"), url(s, "access"), url(s, "integrations"),
        reverse("scheduling:school_billing", args=[s.school.slug]),
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


@pytest.mark.parametrize("use_incoming", [False, True])
def test_party_and_camp_share_workshop_stock(stock, use_incoming):
    s = stock
    if use_incoming:
        s.level.on_hand = 0
        s.level.save()
        incoming(s)
    party = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="p@example.invalid",
        starts_at=s.session.starts_at+timedelta(days=3), ends_at=s.session.ends_at+timedelta(days=3), room=s.room,
        group_size=3, space="shared", status="reviewed", proposed_total=100, proposed_deposit=0,
        materials=[{"item_id": s.item.pk, "name": s.item.name, "quantity": 3}], offering_snapshot={"workshop": "Party", "timezone": "UTC"},
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b")
    booked = reservations.create_reservation(s.user, s.school.slug, "party", party.public_id)
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 2
    with pytest.raises(ValidationError, match="only 2"): reserve(s, 3)
    party2 = PartyRequest.objects.get(pk=party.pk); party2.pk = None; party2.public_id = uuid.uuid4(); party2.submission_key = uuid.uuid4(); party2.materials = []; party2.save()
    pending = reservations.create_reservation(s.user, s.school.slug, "party", party2.public_id)
    assert pending.snapshot["guest_selections_pending"]
    assert not pending.stock_allocations.exists()
    inventory.ensure_stock(pending)
    with pytest.raises(ValidationError, match="guest designs"):
        inventory.validate_allocations(pending)
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
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 1
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
    assert count_result.url == count_url
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
        "group_size": 2, "space": "shared", "materials-TOTAL_FORMS": 2, "materials-INITIAL_FORMS": 0,
        "materials-0-design": s.cat.pk, "materials-0-color": s.blue.pk, "materials-0-guest_name": "Alex",
        "materials-1-design": s.cat.pk, "materials-1-color": s.blue.pk}
    assert s.client.post(target, {**data, "group_size": 1}).status_code == 400
    assert s.client.post(target, {**data, "materials-0-design": 999999}).status_code == 400
    result = s.client.post(target, data)
    assert result.status_code == 302
    party = PartyRequest.objects.get()
    assert [row["quantity"] for row in party.materials] == [1, 1]
    assert party.materials[0]["guest_name"] == "Alex"
    review = url(s, "party_detail", public_id=party.public_id)
    assert b"Participant designs &amp; colors" in s.client.get(review).content
    assert s.client.get(review).context["form"].material_forms.forms[0]["guest_name"].value() == "Alex"
    review_data = {"revision": 0, "status": "reviewed", "duration_minutes": 60, "room": s.room.pk, "proposed_total": 100, "proposed_deposit": 0,
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


def incoming(s, quantity=5, days=2):
    from scheduling.models import IncomingDelivery
    return IncomingDelivery.objects.create(level=s.level, quantity=quantity,
        arrives_on=inventory.StockForecast.today(s.level)+timedelta(days=days))


def delivery_post(s, action, delivery=None, **values):
    query = {"location": s.location.pk, "delivery_action": action}
    if delivery:
        query["delivery_id"] = delivery.pk
    response = s.client.get(url(s, "inventory_stock", pk=s.level.pk), query)
    assert response.status_code == 200
    form = response.context["delivery_form"]
    data = {field.name: field.value() for field in form}
    data.update(action=action, delivery_id=delivery.pk if delivery else "", **values)
    return s.client.post(url(s, "inventory_stock", pk=s.level.pk), data), data


@pytest.mark.parametrize("days", [0, 2])
def test_inline_stock_entry_date_and_retry(stock, days):
    s = stock
    before = s.level.on_hand
    response, data = delivery_post(s, "add", quantity=3,
        arrives_on=inventory.StockForecast.today(s.level)+timedelta(days=days))
    assert response.status_code == 302
    s.level.refresh_from_db()
    assert s.level.on_hand == before + (3 if days == 0 else 0)
    repeat = s.client.post(url(s, "inventory_stock", pk=s.level.pk), data)
    assert repeat.status_code == 400
    s.level.refresh_from_db()
    assert s.level.on_hand == before + (3 if days == 0 else 0)


def test_inline_receipt_button_uses_revision(stock):
    s = stock
    delivery = incoming(s)
    page = s.client.get(url(s, "inventory_stock", pk=s.level.pk))
    assert b"Mark received" in page.content
    assert b"Add expected delivery" not in page.content
    row = next(d for d in page.context["deliveries"] if d.pk == delivery.pk)
    data = {f.name: f.value() for f in row.receipt_form}
    data.update(action="receive", delivery_id=delivery.pk, amount=delivery.remaining)
    before = s.level.on_hand
    assert s.client.post(url(s, "inventory_stock", pk=s.level.pk), data).status_code == 302
    assert s.client.post(url(s, "inventory_stock", pk=s.level.pk), data).status_code == 400
    s.level.refresh_from_db()
    assert s.level.on_hand == before + delivery.quantity


def test_incoming_stock_is_finite_dated_and_not_physical(stock):
    s = stock
    s.level.on_hand = 0; s.level.save()
    incoming(s)
    before = timezone.now()+timedelta(days=1)
    assert inventory.available(s.level, starts_at=before) == 0
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 5
    assert inventory.available(s.level) == 0
    assert inventory.availability_payload(s.offering, starts_at=s.session.starts_at)["materials"][str(s.material.pk)] == 5000
    booking = reserve(s, 5); confirm(s, booking)
    inventory.ensure_stock(booking)
    with pytest.raises(ValidationError, match="enough stock"):
        inventory.consume(s.user, s.school.slug, booking.public_id)
    from scheduling import preparation
    booking.refresh_from_db()
    form = preparation.form_for(booking, s.profile)
    data = {field.name: field.value() for field in form.hidden_fields()}
    with pytest.raises(ValidationError, match="enough stock"):
        preparation.update(s.user, s.school.slug, booking.public_id, {**data, "action": "prepare"})
    with pytest.raises(ValidationError, match="only 0"):
        reserve(s)
    s.level.refresh_from_db()
    assert s.level.on_hand == 0


def test_forecast_preserves_later_commitments_and_buffer(stock):
    s = stock
    incoming(s, quantity=5, days=40)
    later = reserve(s, 5)
    # Later supply cannot fund a day-30 booking or release its existing commitment.
    assert inventory.available(s.level, starts_at=timezone.now()+timedelta(days=5)) == 0
    later.starts_at += timedelta(days=20); later.ends_at += timedelta(days=20); later.save()
    assert inventory.available(s.level, starts_at=timezone.now()+timedelta(days=5)) == 5
    s.location.inventory_buffer_days = 15; s.location.save()
    s.level.location = s.location
    # The day-50 booking now needs materials by day 35, before the delivery.
    assert inventory.available(s.level, starts_at=timezone.now()+timedelta(days=5)) == 0


def test_receipt_partial_retry_cancel_and_audit(stock):
    from core.models import AdminAuditLog
    s = stock
    s.level.on_hand = 0; s.level.save()
    delivery = incoming(s)
    booking = reserve(s, 5); confirm(s, booking)
    result, data = delivery_post(s, "receive", delivery, amount=2)
    assert result.status_code == 302
    retry = s.client.post(url(s, "inventory_stock", pk=s.level.pk), data)
    assert retry.status_code == 400
    delivery.refresh_from_db(); s.level.refresh_from_db()
    assert delivery.received == s.level.on_hand == 2
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 0
    inventory.ensure_stock(booking)
    assert StockAdjustment.objects.get().delta == 2
    result, _ = delivery_post(s, "cancel", delivery)
    assert result.status_code == 302
    delivery.refresh_from_db(); s.level.refresh_from_db()
    assert delivery.canceled and s.level.on_hand == 2
    with pytest.raises(ValidationError, match="enough stock"):
        inventory.ensure_stock(booking)
    response = s.client.get(url(s, "settings"))
    assertContains(response, "Bookings needing stock review")
    assert response.context["shortages"][0]["reservation"].pk == booking.pk
    booking.refresh_from_db()
    assert booking.state == "confirmed"
    assert AdminAuditLog.objects.filter(model_label="scheduling.incomingdelivery").count() == 2


def test_received_delivery_is_not_counted_twice_and_completion_retries(stock):
    s = stock
    s.level.on_hand = 0; s.level.save()
    delivery = incoming(s)
    booking = reserve(s, 5); confirm(s, booking)
    result, _ = delivery_post(s, "receive", delivery, amount=5)
    assert result.status_code == 302
    s.level.refresh_from_db()
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 0
    assert inventory.available(s.level, exclude=booking.pk, starts_at=s.session.starts_at) == 5
    inventory.consume(s.user, s.school.slug, booking.public_id)
    inventory.consume(s.user, s.school.slug, booking.public_id)
    s.level.refresh_from_db()
    assert s.level.on_hand == 0
    assert StockAdjustment.objects.count() == 2


def test_delayed_overdue_and_canceled_supply_does_not_support_new_sales(stock):
    s = stock
    s.level.on_hand = 0; s.level.save()
    delivery = incoming(s)
    booking = reserve(s, 5); confirm(s, booking)
    result, _ = delivery_post(s, "edit", delivery, quantity=5,
        arrives_on=(inventory.StockForecast.today(s.level)+timedelta(days=40)).isoformat())
    assert result.status_code == 302
    with pytest.raises(ValidationError): inventory.ensure_stock(booking)
    delivery.refresh_from_db()
    delivery.arrives_on = inventory.StockForecast.today(s.level)-timedelta(days=1); delivery.save()
    assert inventory.available(s.level, starts_at=s.session.starts_at) == 0
    assertContains(s.client.get(url(s, "settings")), "Overdue")
    result, _ = delivery_post(s, "receive", delivery, amount=5)
    assert result.status_code == 302  # Overdue deliveries can still actually arrive.
    inventory.ensure_stock(booking)


@pytest.mark.parametrize("values", [{"quantity": 0}, {"quantity": -1}, {"quantity": "1.5"},
    {"quantity": "NaN"}, {"quantity": 1000001}, {"arrives_on": "bad"}])
def test_delivery_create_validation(stock, values):
    s = stock
    defaults = dict(material=s.material.pk, quantity=5,
        arrives_on=(inventory.StockForecast.today(s.level)+timedelta(days=2)).isoformat())
    response, _ = delivery_post(s, "new", **{**defaults, **values})
    assert response.status_code == 400
    assert not s.level.deliveries.exists()


def test_delivery_create_inherits_location_and_rejects_duplicate_submission(stock):
    s = stock
    other = Location.objects.create(profile=s.profile, name="Other studio")
    result, data = delivery_post(s, "new", material=999999, location=other.pk,
        quantity=5, arrives_on=(inventory.StockForecast.today(s.level)+timedelta(days=2)).isoformat())
    assert result.status_code == 302
    delivery = s.level.deliveries.get()
    assert delivery.level.location_id == s.location.pk
    assert s.client.post(url(s, "inventory_stock", pk=s.level.pk), data).status_code == 400
    assert s.level.deliveries.count() == 1
    page = s.client.get(url(s, "settings"), {"delivery_action": "new", "location": ""})
    assert page.status_code == 404
    page = s.client.get(url(s, "inventory_stock", pk=s.level.pk), {"delivery_action": "new"})
    assert set(page.context["delivery_form"].fields) == {"quantity", "arrives_on", "submission_key"}


@pytest.mark.parametrize("amount", [0, -1, "1.5", 6, "NaN"])
def test_delivery_receipt_validation(stock, amount):
    s = stock
    delivery = incoming(s)
    result, _ = delivery_post(s, "receive", delivery, amount=amount)
    assert result.status_code == 400
    s.level.refresh_from_db(); delivery.refresh_from_db()
    assert s.level.on_hand == 5 and delivery.received == 0


def test_delivery_permissions_and_location_boundary(stock):
    s = stock
    delivery = incoming(s)
    second = Location.objects.create(profile=s.profile, name="Other")
    for role in ("viewer", "scheduler"):
        SchedulingMembership.objects.filter(user=s.user).update(role=role)
        assert s.client.get(url(s, "inventory_stock", pk=s.level.pk), {"delivery_action": "new"}).status_code == 404
        assert s.client.post(url(s, "inventory_stock", pk=s.level.pk), {"action": "receive", "delivery_id": delivery.pk}).status_code == 404
        assert s.client.get(url(s, "settings"), {"delivery_action": "new"}).status_code == 404
        assert s.client.post(url(s, "settings"), {"action": "receive", "delivery_id": delivery.pk}).status_code == 404
    SchedulingMembership.objects.filter(user=s.user).update(role="owner")
    other_level = StockLevel.objects.create(location=second, material=s.material)
    for action in ("edit", "receive", "cancel"):
        assert s.client.get(url(s, "inventory_stock", pk=other_level.pk), {"delivery_action": action, "delivery_id": delivery.pk}).status_code == 404
        assert s.client.post(url(s, "inventory_stock", pk=other_level.pk), {"action": action, "delivery_id": delivery.pk}).status_code == 404
    assert s.client.get(url(s, "settings"), {"location": second.pk, "delivery_action": "edit", "delivery_id": delivery.pk}).status_code == 404
    for value in ("invalid", "-1", "999999999999999999999999"):
        assert s.client.get(url(s, "settings"), {"delivery_action": "edit", "delivery_id": value}).status_code == 404


def test_buffer_save_stale_edit_and_forecast_query_count(stock, django_assert_num_queries):
    s = stock
    path = url(s, "settings")+f"?location={s.location.pk}"
    response = s.client.get(path)
    data = {"action": "buffer", "inventory_buffer_days": 3,
        "edit_revision": response.context["buffer_form"]["edit_revision"].value()}
    assert s.client.post(path, data).status_code == 302
    assert s.client.post(path, {**data, "inventory_buffer_days": 4}).status_code == 400
    s.location.refresh_from_db()
    assert s.location.inventory_buffer_days == 3
    levels = list(StockLevel.objects.select_related("location", "material"))
    with django_assert_num_queries(2):
        forecast = inventory.StockForecast(levels)
        for _ in range(10):
            forecast.available(levels[0], s.session.starts_at)
            forecast.shortages()


@pytest.mark.django_db(transaction=True)
def test_concurrent_bookings_share_only_finite_incoming_supply(stock):
    s = stock
    s.level.on_hand = 0; s.level.save()
    incoming(s, quantity=1)
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
        assert sorted(executor.map(book, sources)) == [False, True]
    assert StockAllocation.objects.count() == 1


def test_incoming_delivery_isolation(stock):
    s = stock
    delivery = incoming(s)
    other = Location.objects.create(profile=s.profile, name="Restricted studio")
    membership = SchedulingMembership.objects.get(user=s.user)
    membership.all_locations = False
    membership.save()
    membership.locations.set([other])
    assert s.client.get(url(s, "inventory_stock", pk=s.level.pk), {"delivery_action": "new"}).status_code == 404
    assert s.client.post(url(s, "inventory_stock", pk=s.level.pk), {"action": "receive", "delivery_id": delivery.pk}).status_code == 404
    page = s.client.get(url(s, "settings"), {"location": ""})
    assert page.status_code == 200
    assert not list(page.context["deliveries"])
    for action in ("edit", "receive", "cancel"):
        assert s.client.get(url(s, "settings"), {"delivery_action": action, "delivery_id": delivery.pk}).status_code == 404
        assert s.client.post(url(s, "settings"), {"action": action, "delivery_id": delivery.pk}).status_code == 404
    other_school = School.objects.create(slug="other-inventory-school")
    other_profile = SchedulingProfile.objects.create(school=other_school, enabled=True)
    membership.profile = other_profile
    membership.all_locations = True
    membership.save()
    assert s.client.get(url(s, "settings")).status_code == 404
    assert s.client.get(url(s, "inventory_stock", pk=s.level.pk), {"delivery_action": "new"}).status_code == 404
    path = reverse("scheduling:settings", kwargs={"school_slug": other_school.slug})
    assert s.client.get(path, {"delivery_action": "edit", "delivery_id": delivery.pk}).status_code == 404


def test_stock_adjustment_respects_dated_commitments(stock):
    s = stock
    incoming(s, quantity=10)
    reserve(s, 8)
    updated = inventory.adjust(s.user, s.school.slug, s.level.pk, on_hand=4, previous=5, reason="Physical count")
    assert updated.on_hand == 4
    from scheduling.models import IncomingDelivery
    IncomingDelivery.objects.update(canceled=True)
    with pytest.raises(ValidationError, match="reserved materials"):
        inventory.adjust(s.user, s.school.slug, s.level.pk, on_hand=3, previous=4, reason="Physical count")


def test_incoming_stock_uses_studio_date_and_buffer(stock, monkeypatch):
    from datetime import datetime, date, timezone as datetime_timezone
    s = stock
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 10, 24, 23, tzinfo=datetime_timezone.utc))
    s.location.timezone_name = "America/Los_Angeles"
    s.location.inventory_buffer_days = 1
    s.location.save()
    s.level.on_hand = 0
    s.level.save()
    delivery = incoming(s)
    delivery.arrives_on = date(2026, 11, 1)
    delivery.save()
    forecast = inventory.StockForecast([s.level])
    # UTC November 2 is still November 1 in the studio after the DST transition.
    assert forecast.available(s.level, datetime(2026, 11, 2, 1, tzinfo=datetime_timezone.utc)) == 0
    assert forecast.available(s.level, datetime(2026, 11, 2, 18, tzinfo=datetime_timezone.utc)) == 5


@pytest.mark.parametrize("days", [-1, 366, "invalid"])
def test_buffer_rejects_invalid_days(stock, days):
    s = stock
    path = url(s, "settings")+f"?location={s.location.pk}"
    page = s.client.get(path)
    response = s.client.post(path, {"action": "buffer", "inventory_buffer_days": days,
        "edit_revision": page.context["buffer_form"]["edit_revision"].value()})
    assert response.status_code == 400
    s.location.refresh_from_db()
    assert s.location.inventory_buffer_days == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_receipt_does_not_double_count(stock):
    s = stock
    delivery = incoming(s)
    page = s.client.get(url(s, "inventory_stock", pk=s.level.pk), {"location": s.location.pk,
        "delivery_action": "receive", "delivery_id": delivery.pk})
    data = {"action": "receive", "delivery_id": delivery.pk, "amount": 2,
        "edit_revision": page.context["delivery_form"]["edit_revision"].value()}
    def receive(_):
        close_old_connections()
        try:
            client = Client()
            client.force_login(s.user)
            return client.post(url(s, "inventory_stock", pk=s.level.pk), data).status_code
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(receive, range(2))) == [302, 400]
    delivery.refresh_from_db(); s.level.refresh_from_db()
    assert delivery.received == 2 and s.level.on_hand == 7
    assert StockAdjustment.objects.filter(level=s.level).count() == 1
