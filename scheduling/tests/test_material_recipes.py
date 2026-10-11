from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.urls import reverse
from core.models import School, AdminAuditLog
from scheduling import inventory, preparation, completion, requests
from scheduling.inventory_models import validate_amount
from scheduling.models import Material, MaterialRequirement, CatalogVariant, ChoiceOption, InventoryCatalog, Location, SchedulingProfile, StockLevel, StockAllocation, Reservation
from scheduling.tests.test_inventory import stock, reserve, confirm, selections, url, post_form

pytestmark = pytest.mark.django_db


def raw(s):
    fabric = Material.objects.create(profile=s.profile, name="Blue fleece", sku="BLUE", unit="m")
    filling = Material.objects.create(profile=s.profile, name="Stuffing", sku="FILL", unit="g")
    s.item.requirements.all().delete()
    MaterialRequirement.objects.create(variant=s.item, material=fabric, quantity=".5")
    MaterialRequirement.objects.create(variant=s.item, material=filling, quantity=150)
    a = StockLevel.objects.create(material=fabric, location=s.location, on_hand=2)
    b = StockLevel.objects.create(material=filling, location=s.location, on_hand=450)
    return fabric, filling, a, b


def recipe_post(s, variant=None):
    variant = variant or s.item
    target = url(s, "catalog_recipe", catalog_pk=s.catalog.pk, pk=variant.pk)
    page = s.client.get(target)
    rows = list(variant.requirements.all())
    data = {"recipe_revision": page.context["revision"]["recipe_revision"].value(),
            "recipe-TOTAL_FORMS": len(rows), "recipe-INITIAL_FORMS": len(rows)}
    for i, row in enumerate(rows):
        data.update({f"recipe-{i}-id": row.pk, f"recipe-{i}-material": row.material_id, f"recipe-{i}-quantity": str(row.quantity)})
    return target, data


def test_group_booking_aggregates_raw_materials_and_freezes_recipe(stock):
    s = stock; fabric, filling, a, b = raw(s)
    booking = reserve(s, 2)
    assert inventory.available(a) == 1 and inventory.available(b) == 150
    assert {r["name"]: Decimal(r["quantity"]) for r in booking.snapshot["materials"]} == {"Blue fleece": 1, "Stuffing": 300}
    MaterialRequirement.objects.filter(variant=s.item, material=filling).update(quantity=200)
    confirm(s, booking); booking.refresh_from_db()
    preparation.update(s.user, s.school.slug, booking.public_id,
        {"action": "prepare", "edit_revision": preparation.form_for(booking, s.profile)["edit_revision"].value()})
    assert StockLevel.objects.get(pk=b.pk).on_hand == 450
    completion.complete(s.user, s.school.slug, booking.public_id)
    completion.complete(s.user, s.school.slug, booking.public_id)
    b.refresh_from_db(); assert b.on_hand == 150
    assert StockAllocation.objects.filter(consumed_at__isnull=False).count() == 2


def test_different_designs_and_colors_compete_for_shared_stuffing(stock):
    s = stock; fabric, filling, a, b = raw(s)
    red = ChoiceOption.objects.create(group=s.color, label="Red")
    dog = ChoiceOption.objects.create(group=s.design, label="Dog")
    other = CatalogVariant(catalog=s.catalog, name="Red dog", option_ids=[dog.pk, red.pk]); other.full_clean(); other.save()
    MaterialRequirement.objects.create(variant=other, material=filling, quantity=200)
    with pytest.raises(ValidationError, match="Stuffing"):
        inventory.check_items(s.offering, {s.item.pk: 2, other.pk: 1})
    counts = inventory.check_items(s.offering, {s.item.pk: 1, other.pk: 1})
    assert dict((level.material_id, quantity) for level, quantity in counts)[filling.pk] == 350
    payload = inventory.availability_payload(s.offering)
    assert payload["materials"][str(filling.pk)] == 450000
    assert next(v for v in payload["variants"] if red.pk in v["options"])["requirements"][str(filling.pk)] == 200000


@pytest.mark.django_db(transaction=True)
def test_concurrent_different_designs_cannot_overbook_shared_material(stock):
    from django.db import close_old_connections
    from scheduling import reservations
    from scheduling.tests.test_inventory import request
    s = stock
    fabric, filling, a, b = raw(s)
    b.on_hand = 150; b.save()
    dog = ChoiceOption.objects.create(group=s.design, label="Dog")
    variant = CatalogVariant(catalog=s.catalog, name="Blue dog", option_ids=[dog.pk, s.blue.pk])
    variant.full_clean(); variant.save()
    MaterialRequirement.objects.create(variant=variant, material=filling, quantity=150)
    first, second = request(s), request(s)
    child = second.participants.get()
    child.selections[0].update(option_id=dog.pk, option="Dog")
    child.save()
    barrier = Barrier(2)

    def book(source):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id,
                total_cents=100, deposit_cents=0)
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, (first, second)))
    assert sorted(results) == [False, True]
    assert inventory.reserved(b) == 150
    assert inventory.available(b) == 0
    assert Reservation.objects.count() == 1


@pytest.mark.parametrize("value,expected", [(Decimal(".500"), "0.5"), (Decimal("18.500"), "18.5"),
    (Decimal("150.000"), "150"), (Decimal(".001"), "0.001"), (Decimal("-2.500"), "-2.5"),
    (0, "0"), (None, ""), ("nan", ""), ("invalid", "")])
def test_material_amount_is_readable_without_losing_precision(value, expected):
    from scheduling.templatetags.material_numbers import material_amount
    assert material_amount(value) == expected


def test_catalog_does_not_call_archived_material_recipe_ready(stock):
    s = stock
    s.material.is_active = False; s.material.save()
    page = s.client.get(url(s, "inventory_catalog", catalog_pk=s.catalog.pk))
    assert b"Needs attention" in page.content
    assert not page.context["designs"][0]["variants"][0].recipe_ready


def test_raw_material_migration_refuses_lossy_rollback(stock):
    from importlib import import_module
    from types import SimpleNamespace
    from django.apps import apps
    from django.db.migrations.exceptions import IrreversibleError
    migration = import_module("scheduling.migrations.0022_material_recipes")
    schema = SimpleNamespace(connection=SimpleNamespace(alias="default"))
    migration.check_legacy_rollback(apps, schema)
    raw(stock)
    with pytest.raises(IrreversibleError, match="cannot be converted"):
        migration.check_legacy_rollback(apps, schema)


def test_party_and_multi_session_camp_share_fractional_raw_materials(stock):
    import uuid
    from datetime import timedelta
    from django.http import QueryDict
    from scheduling import reservations, camps
    from scheduling.models import PartyRequest, Camp, Session
    s = stock
    fabric, filling, a, b = raw(s)
    party = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="p@example.invalid",
        starts_at=s.session.starts_at + timedelta(days=3), ends_at=s.session.ends_at + timedelta(days=3),
        room=s.room, group_size=2, space="shared", status="reviewed", proposed_total=100, proposed_deposit=0,
        materials=[{"item_id": s.item.pk, "name": s.item.name, "quantity": 2}],
        offering_snapshot={"workshop": "Party", "timezone": "UTC"}, submission_key=uuid.uuid4(),
        submission_digest="d", browser_digest="b")
    booked = reservations.create_reservation(s.user, s.school.slug, "party", party.public_id)
    assert inventory.available(a) == 1 and inventory.available(b) == 150
    second = Session.objects.create(offering=s.offering, room=s.room,
        starts_at=s.session.starts_at + timedelta(days=1), ends_at=s.session.ends_at + timedelta(days=1),
        capacity=100, is_published=True)
    camp = Camp.objects.create(offering=s.offering, name="Camp", price=10, deposit=0, is_open=True)
    camp.sessions.set([s.session, second])
    catalog = requests.catalog_for(s.offering)
    data = QueryDict(mutable=True)
    data.update({"request_token": camps.registration_token(camp, camps.camp_dates(camp), catalog, "b"),
        "purchaser_name": "Parent", "purchaser_email": "p@example.invalid",
        "participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0", "participants-0-name": "Child",
        f"participants-0-choice_{s.design.pk}": str(s.cat.pk), f"participants-0-choice_{s.color.pk}": str(s.blue.pk)})
    registered, _, _ = camps.register_camp(s.school.slug, camp.pk, data, "b")
    assert inventory.available(a) == Decimal(".5") and inventory.available(b) == 0
    assert registered.reservation.stock_allocations.count() == 2  # One recipe, not one per camp date.
    with pytest.raises(ValidationError, match="Stuffing"):
        reserve(s)
    reservations.cancel_reservation(s.user, s.school.slug, booked.public_id)
    assert inventory.available(b) == 300
    confirm(s, registered.reservation)
    completion.complete(s.user, s.school.slug, registered.reservation.public_id)
    b.refresh_from_db(); assert b.on_hand == 300


@pytest.mark.parametrize("value,unit", [("nan", "g"), ("inf", "m"), ("-1", "g"), ("1.2", "pieces"), ("0", "g"), (".0001", "m"), ("1000001", "g"), ("oops", "g"), (True, "g")])
def test_bad_amounts_are_rejected(value, unit):
    with pytest.raises(ValidationError): validate_amount(value, unit)


def test_amounts_and_material_unit_immutability(stock):
    s = stock
    assert validate_amount(".125", "m") == Decimal(".125")
    assert validate_amount(0, "pieces", allow_zero=True) == 0
    s.material.unit = "g"
    with pytest.raises(ValidationError): s.material.full_clean()


def test_missing_archived_or_foreign_recipe_blocks_new_bookings(stock):
    s = stock
    s.item.requirements.all().delete()
    with pytest.raises(ValidationError, match="no material recipe"): reserve(s)
    assert not Reservation.objects.exists()
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-material"))
    foreign = Material.objects.create(profile=other, name="Foreign", sku="F")
    row = MaterialRequirement(variant=s.item, material=foreign, quantity=1)
    with pytest.raises(ValidationError, match="belong"): row.full_clean()
    row.save()  # Even bad imported data cannot leak another tenant's material.
    with pytest.raises(ValidationError, match="unavailable"): reserve(s)
    row.delete()
    MaterialRequirement.objects.create(variant=s.item, material=s.material, quantity=1)
    s.material.is_active = False; s.material.save()
    with pytest.raises(ValidationError): reserve(s)
    assert inventory.availability_payload(s.offering)["variants"] == []


def test_archiving_design_after_booking_does_not_break_preparation(stock):
    s = stock; booking = reserve(s); confirm(s, booking)
    s.item.is_active = False; s.item.save()
    s.material.is_active = False; s.material.name = "Renamed"; s.material.save()
    booking.refresh_from_db()
    from scheduling.reservation_workspace import prep_context
    assert prep_context(booking, s.profile)["allocations"][0].material_name == "Blue cat fabric panels"
    completion.complete(s.user, s.school.slug, booking.public_id)
    assert Reservation.objects.get(pk=booking.pk).state == "completed"


def test_missing_or_tampered_snapshot_cannot_be_completed(stock):
    s = stock; booking = reserve(s); confirm(s, booking)
    booking.refresh_from_db(); booking.snapshot["materials"][0]["quantity"] = "2"; booking.save()
    with pytest.raises(ValidationError, match="do not match"): completion.complete(s.user, s.school.slug, booking.public_id)
    booking.snapshot.pop("material_catalog_id"); booking.save()
    with pytest.raises(ValidationError, match="missing its saved"): inventory.ensure_stock(booking)
    assert not booking.stock_allocations.get().consumed_at


def test_recipe_crud_stale_submission_empty_recipe_and_unit_validation(stock):
    s = stock
    target, data = recipe_post(s)
    data["recipe-0-quantity"] = "2"
    assert s.client.post(target, data).status_code == 302
    assert s.item.requirements.get().quantity == 2
    assert s.client.post(target, data).status_code == 400
    target, data = recipe_post(s)
    assert s.client.post(target, {**data, "recipe-0-quantity": ".5"}).status_code == 400
    assert s.client.post(target, {**data, "recipe-0-DELETE": "on"}).status_code == 400
    assert s.client.post(target, {**data, "recipe-0-material": "99999"}).status_code == 400
    assert s.item.requirements.get().quantity == 2
    audit = AdminAuditLog.objects.filter(model_label="scheduling.catalogvariant").latest("pk")
    assert audit.changes["recipe"]["to"] == [[s.material.pk, "2.000"]]


def test_recipe_add_remove_and_foreign_id_injection(stock):
    s = stock; fabric, filling, a, b = raw(s)
    target, data = recipe_post(s)
    data["recipe-TOTAL_FORMS"] = 3
    data.update({"recipe-2-material": s.material.pk, "recipe-2-quantity": "2", "recipe-0-DELETE": "on"})
    assert s.client.post(target, data).status_code == 302
    assert s.item.requirements.count() == 2
    target, data = recipe_post(s)
    data["recipe-0-id"] = "999999"
    assert s.client.post(target, data).status_code == 400


def test_material_crud_receive_counts_and_location_creation(stock):
    s = stock
    assert s.client.post(url(s, "material_new"), {"name": "Fleece", "sku": "M1", "unit": "m", "is_active": "on"}).status_code == 302
    material = Material.objects.get(sku="M1"); level = material.levels.get()
    target = url(s, "inventory_stock", pk=level.pk)
    assert s.client.post(target, {"mode": "receive", "on_hand": "3.5", "previous": 0, "reason": "Delivery"}).status_code == 302
    level.refresh_from_db(); assert level.on_hand == Decimal("3.5")
    assert s.client.post(target, {"mode": "count", "on_hand": "2.25", "previous": "3.5", "reason": "Count"}).status_code == 302
    level.refresh_from_db(); assert level.on_hand == Decimal("2.25")
    assert s.client.post(target, {"mode": "receive", "on_hand": "3", "previous": "3.5", "reason": "Stale delivery"}).status_code == 400
    assert material.levels.get().adjustments.count() == 2
    assert post_form(s.client, url(s, "material_edit", pk=material.pk), {"name": "Soft fleece", "sku": "M1", "unit": "pieces", "is_active": "on"}).status_code == 302
    material.refresh_from_db(); assert material.unit == "m"
    from scheduling.setup import save_setup
    form = save_setup(s.user, s.school.slug, "locations", {"name": "East", "timezone_name": "UTC"})
    assert form.is_valid(), form.errors
    assert StockLevel.objects.filter(location=form.instance).count() == 2


def test_design_create_color_changes_and_stale_protection(stock):
    s = stock
    red = ChoiceOption.objects.create(group=s.color, label="Red")
    target = url(s, "catalog_design_new", catalog_pk=s.catalog.pk)
    assert s.client.post(target, {"label": "Bear", "colors": [s.blue.pk, red.pk], "is_active": "on"}).status_code == 302
    design = s.design.options.get(label="Bear")
    variants = [v for v in s.catalog.items.all() if design.pk in v.option_ids]
    assert len(variants) == 2 and all(not v.requirements.exists() for v in variants)
    assert StockLevel.objects.count() == 1  # No finished plushie stock is created.
    target = url(s, "catalog_design_edit", catalog_pk=s.catalog.pk, pk=design.pk)
    page = s.client.get(target)
    data = {"label": "Bear", "colors": [red.pk], "is_active": "on", **{f.name: f.value() for f in page.context["form"].hidden_fields()}}
    assert s.client.post(target, data).status_code == 302
    assert s.client.post(target, data).status_code == 400
    assert CatalogVariant.objects.filter(pk__in=[v.pk for v in variants], is_active=True).count() == 1


def test_foreign_material_catalog_and_recipe_urls_are_hidden(stock):
    s = stock
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="private-materials"))
    material = Material.objects.create(profile=other, name="Private", sku="X")
    catalog = InventoryCatalog.objects.create(profile=other, name="Private")
    assert s.client.get(url(s, "material_edit", pk=material.pk)).status_code == 404
    assert s.client.post(url(s, "material_edit", pk=material.pk), {}).status_code == 404
    assert s.client.get(url(s, "catalog_design_new", catalog_pk=catalog.pk)).status_code == 404
    target, data = recipe_post(s)
    assert s.client.post(target, {**data, "recipe-0-material": material.pk}).status_code == 400
    assert s.client.get(url(s, "catalog_recipe", catalog_pk=catalog.pk, pk=s.item.pk)).status_code == 404


def test_recipe_digest_changes_without_stock_adjustments_invalidating_form(stock):
    s = stock; groups = requests.catalog_for(s.offering)
    before = requests.catalog_digest(s.session, groups)
    s.level.on_hand = 6; s.level.save()
    assert requests.catalog_digest(s.session, groups) == before
    MaterialRequirement.objects.filter(variant=s.item).update(quantity=2)
    assert requests.catalog_digest(s.session, groups) != before


def test_reset_is_local_guarded_and_preserves_other_tenants(stock):
    from scheduling.experience_demo.reset import reset_demo
    s = stock
    with patch("scheduling.experience_demo.reset.local_database", return_value=True):
        with pytest.raises(CommandError): reset_demo(s.profile)
    s.school.slug = "plushumi-demo"; s.school.is_demo = True
    s.school.feature_flags = {"booking_experience_demo": True}; s.school.save()
    with patch("scheduling.experience_demo.reset.local_database", return_value=False):
        with pytest.raises(CommandError): reset_demo(s.profile)
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="unaffected-school"))
    foreign = Material.objects.create(profile=other, name="Private", sku="X")
    booking = reserve(s); confirm(s, booking)
    with patch("scheduling.experience_demo.reset.local_database", return_value=True):
        reset_demo(s.profile)
    assert Material.objects.filter(pk=foreign.pk).exists()
    assert not Reservation.objects.filter(profile=s.profile).exists()
    assert not s.profile.locations.exists()
    assert SchedulingProfile.objects.filter(pk=s.profile.pk).exists()


def test_reset_refuses_connected_payment_accounts(stock):
    from core.payments.models import PaymentAccount
    from scheduling.experience_demo.reset import reset_demo
    s = stock; s.school.slug = "plushumi-demo"; s.school.is_demo = True
    s.school.feature_flags = {"booking_experience_demo": True}; s.school.save()
    PaymentAccount.objects.create(school=s.school, environment="test", reference="acct_connected")
    with patch("scheduling.experience_demo.reset.local_database", return_value=True):
        with pytest.raises(CommandError, match="connected"): reset_demo(s.profile)
    assert s.profile.locations.exists()


def test_local_seed_reset_has_fresh_prepared_and_completed_examples():
    from scheduling.experience_demo import config
    with patch.object(config, "local_database", return_value=True), patch("scheduling.management.commands.seed_booking_experience_demo.local_database", return_value=True), patch("scheduling.experience_demo.reset.local_database", return_value=True):
        call_command("seed_booking_experience_demo", "plushumi-demo", password="admin", reset=True)
    profile = SchedulingProfile.objects.get(school__slug="plushumi-demo")
    assert profile.reservations.count() == 15
    assert profile.reservations.filter(state="completed").count() == 1
    assert profile.reservations.filter(prepared_at__isnull=False).count() == 5
    assert profile.materials.count() == 11
