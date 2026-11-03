import uuid
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from scheduling import guest_lists, preparation, workshop_selections
from scheduling.models import ChoiceOption, CatalogVariant, MaterialRequirement, SchedulingMembership
from scheduling.participant_preparation import rows_for
from scheduling.reservation_notes import update as add_note
from .test_inventory import stock, reserve, confirm, url
from .test_guest_lists import booking, payload
from .test_studio_preparation import query

pytestmark = pytest.mark.django_db


def post(s, item, key, action="ready", **extra):
    item.refresh_from_db()
    return s.client.post(url(s, "studio_preparation_update", public_id=item.public_id), {
        "edit_revision": preparation.form_for(item, s.profile)["edit_revision"].value(),
        "participant": str(key), "action": action, **extra}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


def test_individual_prep_sets_and_clears_reservation_readiness(stock):
    item = reserve(stock, 2); confirm(stock, item)
    people = list(item.workshop_request.participants.all())
    assert post(stock, item, people[0].pk).status_code == 200
    item.refresh_from_db()
    assert [r["prepared"] for r in rows_for(item)] == [True, False]
    assert not item.prepared_at
    page = stock.client.get(url(stock, "studio_preparation"), query(item))
    assert page.context["studios"][0]["totals"][0]["quantity"] == 1
    assert post(stock, item, people[1].pk).status_code == 200
    item.refresh_from_db()
    assert item.prepared_at and item.prepared_by == stock.user
    assert post(stock, item, "extras").status_code == 400
    assert post(stock, item, "", "reopen").status_code == 400
    assert post(stock, item, people[0].pk, "unready").status_code == 200
    item.refresh_from_db()
    assert not item.prepared_at and rows_for(item)[1]["prepared"]
    stock.level.refresh_from_db()
    assert stock.level.on_hand == 5 and item.stock_allocations.get().quantity == 2


def test_partial_party_reserves_and_prepares_only_selected_guests(stock, booking):
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 1, action="draft"))
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    assert booking.stock_allocations.get().quantity == 1
    assert post(stock, booking, "0").status_code == 200
    assert post(stock, booking, "1").status_code == 400
    assert post(stock, booking, "", "prepare").status_code == 400
    assert post(stock, booking, "extras").status_code == 400
    page = stock.client.get(url(stock, "studio_preparation"), query(booking))
    assert not page.context["studios"][0]["totals"]
    assert b"Picks at studio" in page.content
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 2, revision=1))
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    assert rows_for(booking)[0]["prepared"]
    assert booking.stock_allocations.get().quantity == 2
    assert post(stock, booking, "1").status_code == 200
    booking.refresh_from_db()
    assert booking.prepared_at


@pytest.mark.parametrize("action", ["ready", "unready", "prepare", "edit", "nonsense"])
def test_stale_and_missing_revisions_cannot_write(stock, action):
    item = reserve(stock); confirm(stock, item)
    person = item.workshop_request.participants.get()
    token = preparation.form_for(item, stock.profile)["edit_revision"].value()
    assert post(stock, item, person.pk).status_code == 200
    assert post(stock, item, person.pk, action, edit_revision=token).status_code == 400
    assert post(stock, item, person.pk, action, edit_revision="").status_code == 400


@pytest.mark.parametrize("state", ["held", "completed", "canceled", "review"])
def test_closed_state_and_csrf(stock, state):
    item = reserve(stock)
    item.state = state; item.save()
    assert post(stock, item, "extras").status_code == 400
    client = Client(enforce_csrf_checks=True); client.force_login(stock.user)
    assert client.post(url(stock, "studio_preparation_update", public_id=item.public_id), {}).status_code == 403


def test_permissions_stock_and_invalid_participant(stock):
    item = reserve(stock); confirm(stock, item)
    assert post(stock, item, "invalid").status_code == 400
    assert post(stock, item, "extras", "invalid").status_code == 400
    stock.level.on_hand = 0; stock.level.save()
    assert post(stock, item, item.workshop_request.participants.get().pk).status_code == 400
    stock.level.on_hand = 5; stock.level.save()
    item.stock_allocations.update(consumed_at=timezone.now())
    assert post(stock, item, "extras").status_code == 400
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    assert post(stock, item, "extras").status_code == 404
    endpoint = url(stock, "studio_preparation_update", public_id=item.public_id).replace(stock.school.slug, "other")
    assert stock.client.post(endpoint, {}).status_code == 404


def test_workshop_edit_name_preserves_prep_design_change_resets_only_one(stock):
    item = reserve(stock, 2); confirm(stock, item)
    people = list(item.workshop_request.participants.all())
    for person in people:
        assert post(stock, item, person.pk).status_code == 200
    data = {f"choice_{stock.design.pk}": stock.cat.pk, f"choice_{stock.color.pk}": stock.blue.pk,
        "name": "New name", "selection_revision": workshop_selections.revision(people[0])}
    assert post(stock, item, people[0].pk, "edit", **data).status_code == 200
    item.refresh_from_db(); people[0].refresh_from_db()
    assert item.prepared_at and people[0].name == "New name"
    red = ChoiceOption.objects.create(group=stock.color, label="Red", swatch="#ff0000")
    variant = CatalogVariant.objects.create(catalog=stock.catalog, name="Red cat", option_ids=[stock.cat.pk, red.pk],
        choice_key=":".join(map(str, sorted([stock.cat.pk, red.pk]))))
    MaterialRequirement.objects.create(variant=variant, material=stock.material, quantity=2)
    data.update({f"choice_{stock.color.pk}": red.pk, "selection_revision": workshop_selections.revision(people[0])})
    assert post(stock, item, people[0].pk, "edit", **data).status_code == 200
    item.refresh_from_db()
    assert [r["prepared"] for r in rows_for(item)] == [False, True]
    assert not item.prepared_at and item.stock_allocations.get().quantity == 3
    assert post(stock, item, people[0].pk, "edit", **data).status_code == 400


def test_new_notes_do_not_change_participant_readiness(stock):
    item = reserve(stock); confirm(stock, item)
    assert post(stock, item, item.workshop_request.participants.get().pk).status_code == 200
    form = add_note(stock.user, stock.school.slug, item.public_id, {"body": "Pull extra decorations", "submission_id": uuid.uuid4()})
    assert form.is_valid()
    item.refresh_from_db()
    assert rows_for(item)[0]["prepared"] and item.prepared_at


def test_party_edit_and_stock_failure_are_atomic(stock, booking):
    assert post(stock, booking, "0", "edit", revision=0, guest_name="Alex", guest_age=8,
        design=stock.cat.pk, color=stock.blue.pk).status_code == 200
    assert post(stock, booking, "0").status_code == 200
    stock.level.on_hand = 1; stock.level.save()
    assert post(stock, booking, "1", "edit", revision=1, guest_name="Mei",
        design=stock.cat.pk, color=stock.blue.pk).status_code == 400
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    assert booking.party_request.revision == 1
    assert booking.stock_allocations.get().quantity == 1
    assert rows_for(booking)[0]["prepared"]


def test_party_partial_recipe_frozen_and_removal_releases_materials(stock, booking):
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 1, action="draft"))
    recipe = stock.item.requirements.get(); recipe.quantity = 2; recipe.save()
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 2, revision=1))
    assert booking.stock_allocations.get().quantity == Decimal(2)
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 1, revision=2, action="draft"))
    assert booking.stock_allocations.get().quantity == Decimal(1)
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 0, revision=3, action="draft"))
    assert not booking.stock_allocations.exists()


def test_week_groups_matching_times_notes_and_completed_toggle(stock):
    first = reserve(stock); confirm(stock, first)
    second = reserve(stock); confirm(stock, second)
    first.notes.create(actor=stock.user, body="Internal <script>note</script>")
    page = stock.client.get(url(stock, "studio_preparation"), query(first))
    studio = page.context["studios"][0]
    assert len(studio["days"][0]["slots"][0]["entries"]) == 2
    assert b"Internal &lt;script&gt;note&lt;/script&gt;" in page.content
    assert b"#123456" in page.content
    assert b"Reservation extras" not in page.content
    assert b"Reopen prep" not in page.content
    assert b"Mark all prepared" not in page.content
    second.state = "completed"; second.save()
    page = stock.client.get(url(stock, "studio_preparation"), query(first, completed=1))
    assert len(page.context["studios"][0]["completed_entries"]) == 1
    person = first.workshop_request.participants.get()
    page = stock.client.get(url(stock, "studio_preparation"), query(first, edit=f"{first.public_id}:{person.pk}"))
    assert page.context["studios"][0]["entries"][0]["prep_rows"][0]["form"]


def test_prepare_available_participant_without_waiting_for_future_delivery(stock):
    from datetime import timedelta
    from scheduling.models import IncomingDelivery
    stock.level.on_hand = 1; stock.level.save()
    IncomingDelivery.objects.create(level=stock.level, quantity=1, arrives_on=timezone.localdate() + timedelta(days=2))
    item = reserve(stock, 2); confirm(stock, item)
    people = list(item.workshop_request.participants.all())
    assert post(stock, item, people[0].pk).status_code == 200
    assert post(stock, item, people[1].pk).status_code == 400
    assert post(stock, item, "", "prepare").status_code == 400


def test_legacy_party_draft_requires_saving_materials_before_preparation(stock, booking):
    booking.snapshot["guest_list_draft"] = [{"item_id": stock.item.pk, "name": stock.item.name, "quantity": 1}]
    booking.save()
    assert post(stock, booking, "0").status_code == 400
    assert not booking.stock_allocations.exists()


def test_legacy_single_recipe_can_be_preserved_when_guest_list_changes(stock, booking):
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock))
    booking.refresh_from_db()
    booking.snapshot.pop("party_material_recipes")
    booking.save()
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 1, revision=1, action="draft"))
    assert booking.stock_allocations.get().quantity == 1


def test_legacy_mixed_recipes_do_not_guess_stock_and_cannot_change_studios(stock, booking):
    from django.core.exceptions import ValidationError
    from scheduling.inventory import allocate_party_selections
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock))
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    booking.snapshot.pop("party_material_recipes")
    booking.party_request.materials[1]["item_id"] = 999999
    rows = guest_lists.saved_rows(booking)
    allocate_party_selections(booking, rows)
    with pytest.raises(ValidationError, match="saved material totals"):
        allocate_party_selections(booking, rows[:1])
    booking.snapshot["material_location_id"] = 999999
    with pytest.raises(ValidationError, match="studio changed"):
        allocate_party_selections(booking, rows)


def test_invalid_guest_edit_and_normal_form_redirect(stock, booking):
    assert post(stock, booking, "99", "edit").status_code == 400
    assert post(stock, booking, "0", "edit", revision=0, guest_age="invalid").status_code == 400
    booking.refresh_from_db()
    endpoint = url(stock, "studio_preparation_update", public_id=booking.public_id)
    response = stock.client.post(endpoint, {"action": "unready", "participant": "0",
        "edit_revision": preparation.form_for(booking, stock.profile)["edit_revision"].value()})
    assert response.status_code == 302


def test_print_contains_partial_party_placeholders_and_both_note_types(stock, booking):
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, payload(stock, 1, action="draft"))
    booking.party_request.customer_notes = "Bring <charms>"
    booking.party_request.save(update_fields=["customer_notes"])
    booking.party_request.staff_notes.create(actor=stock.user, body="Table near the window")
    assert post(stock, booking, "0").status_code == 200
    page = stock.client.get(url(stock, "studio_preparation"), query(booking, print=1))
    entry = page.context["studios"][0]["entries"][0]
    assert len(entry["print_rows"]) == 2
    assert entry["print_rows"][0]["prepared"]
    assert b"Bring &lt;charms&gt;" in page.content and b"Table near the window" in page.content
    assert b"Reservation extras" not in page.content
    page = stock.client.get(url(stock, "studio_preparation"), query(booking, edit=f"{booking.public_id}:0"))
    assert b'guest_0_guest_name' in page.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_preparation_updates_reject_the_stale_writer(stock):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    item = reserve(stock); confirm(stock, item)
    item.refresh_from_db()
    data = {"edit_revision": preparation.form_for(item, stock.profile)["edit_revision"].value(),
        "participant": str(item.workshop_request.participants.get().pk), "action": "ready"}
    endpoint = url(stock, "studio_preparation_update", public_id=item.public_id)
    def save(_):
        close_old_connections()
        try:
            client = Client(); client.force_login(stock.user)
            return client.post(endpoint, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(save, range(2))) == [200, 400]
    assert item.stock_allocations.get().quantity == 1


def staff_data(s, item, names):
    item.refresh_from_db()
    return {"staff-staff_names": names,
        "staff-edit_revision": preparation.staff_form_for(item, s.profile)["edit_revision"].value()}


def test_staff_names_are_booking_specific_even_in_same_session(stock):
    first = reserve(stock); confirm(stock, first)
    second = reserve(stock); confirm(stock, second)
    names = "Andrea, Kayla, Chelsea"
    assert first.session_id == second.session_id
    assert post(stock, first, "", "staff", **staff_data(stock, first, names)).status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.staff_names == names and second.staff_names == ""
    page = stock.client.get(url(stock, "studio_preparation"), query(first))
    assert {entry["item"].pk: entry["staff_names"] for entry in page.context["studios"][0]["entries"]} == {first.pk: names, second.pk: ""}
    assert b'prep-count' not in page.content and b'prep-remaining' not in page.content
    assert names.encode() in stock.client.get(url(stock, "studio_preparation"), query(first, print=1)).content
    page = stock.client.get(url(stock, "studio_preparation"), query(first, staff=first.public_id))
    assert b'name="staff-staff_names"' in page.content
    assert post(stock, second, "", "staff", **staff_data(stock, second, "Jo")).status_code == 200
    assert post(stock, first, "", "staff", **staff_data(stock, first, "")).status_code == 200
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.staff_names == "" and second.staff_names == "Jo"


def test_staff_names_party_is_separate_validated_and_escaped(stock, booking):
    workshop = reserve(stock); confirm(stock, workshop)
    names = "Jo <script>alert(1)</script>"
    assert post(stock, booking, "", "staff", **staff_data(stock, booking, names)).status_code == 200
    booking.refresh_from_db()
    assert booking.staff_names == names
    workshop.refresh_from_db()
    assert workshop.staff_names == ""
    page = stock.client.get(url(stock, "studio_preparation"), query(booking))
    assert b"Jo &lt;script&gt;" in page.content and names.encode() not in page.content
    assert post(stock, booking, "", "staff", **staff_data(stock, booking, "a" * 201)).status_code == 400
    booking.refresh_from_db()
    assert booking.staff_names == names


def test_staff_names_reject_stale_booking_and_foreign_tokens(stock, booking):
    first = reserve(stock); confirm(stock, first)
    second = reserve(stock); confirm(stock, second)
    stale = staff_data(stock, first, "Outdated")
    independent = staff_data(stock, second, "Independent")
    assert post(stock, first, "", "staff", **staff_data(stock, first, "Current")).status_code == 200
    assert post(stock, first, "", "staff", **stale).status_code == 400
    assert post(stock, second, "", "staff", **staff_data(stock, first, "Wrong workshop")).status_code == 400
    assert post(stock, second, "", "staff", **independent).status_code == 200
    assert post(stock, booking, "", "staff", **staff_data(stock, first, "Wrong target")).status_code == 400
    assert post(stock, booking, "", "staff", **{"staff-staff_names": "No token"}).status_code == 400
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    assert post(stock, booking, "", "staff", **staff_data(stock, booking, "Viewer")).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_staff_names_migration_preserves_reservation_and_session(stock):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from scheduling.models import Reservation
    item = reserve(stock); confirm(stock, item)
    original = dict(item.snapshot)
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("scheduling", "0048_bookingmessage_created_by_bookingmessage_is_html")])
        MigrationExecutor(connection).migrate(latest)
        saved = Reservation.objects.get(pk=item.pk)
        assert saved.snapshot == original and saved.state == "confirmed" and saved.staff_names == ""
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_booking_staff_migration_preserves_existing_assignments(stock):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from scheduling.models import Reservation
    first = reserve(stock); confirm(stock, first)
    second = reserve(stock); confirm(stock, second)
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0049_preparation_staff_names")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        apps.get_model("scheduling", "Session").objects.filter(pk=first.session_id).update(staff_names="Andrea")
        apps.get_model("scheduling", "Reservation").objects.filter(pk=second.pk).update(staff_names="Jo")
        MigrationExecutor(connection).migrate(latest)
        assert Reservation.objects.get(pk=first.pk).staff_names == "Andrea"
        assert Reservation.objects.get(pk=second.pk).staff_names == "Jo"
    finally:
        MigrationExecutor(connection).migrate(latest)
