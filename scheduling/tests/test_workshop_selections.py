from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404, QueryDict
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone

from scheduling import inventory, requests, reservations, workshop_selections
from scheduling.models import ChoiceGroup, ChoiceOption, SchedulingMembership, StockAllocation
from scheduling.request_forms import ParticipantForm
from scheduling.tests.test_inventory import stock, request as make_request, confirm

pytestmark = pytest.mark.django_db


def pending_booking(s, *, mixed=False):
    source = make_request(s, 2 if mixed else 1)
    person = source.participants.last()
    person.selections, person.selections_deferred = [], True
    person.save()
    item = reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id,
        total_cents=6500, deposit_cents=2500)
    confirm(s, item)
    item.refresh_from_db()
    return item, person


def choices(s):
    return {f"choice_{s.design.pk}": str(s.cat.pk), f"choice_{s.color.pk}": str(s.blue.pk)}


def save(s, item, person, data=None):
    return workshop_selections.update(s.user, s.school.slug, item.public_id, person.pk,
        choices(s) if data is None else data)


def test_forms_require_explicit_opt_in_and_preserve_non_stock_fields(stock):
    s = stock
    age = ChoiceGroup.objects.create(catalog=s.catalog, name="Age", tracks_stock=False)
    option = ChoiceOption.objects.create(group=age, label="Adult")
    catalog = requests.catalog_for(s.offering)
    data = {"name": "Guest", "selections_deferred": "on", f"choice_{age.pk}": str(option.pk)}
    assert not ParticipantForm(data, catalog=catalog).is_valid()
    form = ParticipantForm(data, catalog=catalog, allow_deferred=True)
    assert form.is_valid(), form.errors
    assert form.participant_snapshot()["selections_deferred"] is True
    assert [r["group"] for r in form.snapshot()] == ["Age"]
    del data[f"choice_{age.pk}"]
    assert ParticipantForm(data, catalog=catalog, allow_deferred=True).is_valid()
    unnamed = ParticipantForm({"participants-1-selections_deferred": "on"}, prefix="participants-1", catalog=catalog, allow_deferred=True)
    assert unnamed.is_valid(), unnamed.errors
    assert unnamed.cleaned_data["name"] == "Participant 2"
    assert unnamed.snapshot() == []
    assert not ParticipantForm({"name": "Guest"}, catalog=catalog, allow_deferred=True).is_valid()


def test_submit_mixed_group_retry_and_setting_change(stock):
    s = stock
    s.offering.allow_deferred_selections = True
    s.offering.save()
    catalog = requests.catalog_for(s.offering)
    token = requests.request_token(s.session, catalog, "browser")
    data = QueryDict(mutable=True)
    data.update(request_token=token, purchaser_name="Host", purchaser_email="host@example.invalid",
        **{"participants-TOTAL_FORMS": "2", "participants-INITIAL_FORMS": "0",
           "participants-0-name": "Now", "participants-1-name": "Later", "participants-1-selections_deferred": "on"})
    data.update({"participants-0-" + key: value for key, value in choices(s).items()})
    item, _, _ = requests.submit_request(s.school.slug, s.session.pk, data, "browser")
    assert list(item.participants.values_list("selections_deferred", flat=True)) == [False, True]
    assert requests.submit_request(s.school.slug, s.session.pk, data, "browser")[0].pk == item.pk
    data["request_token"] = requests.request_token(s.session, catalog, "browser")
    s.offering.allow_deferred_selections = False
    s.offering.save()
    with pytest.raises(ValidationError, match="changed"):
        requests.submit_request(s.school.slug, s.session.pk, data, "browser")


@pytest.mark.parametrize("mixed", [False, True])
def test_deferred_booking_stock_and_completion(stock, mixed):
    s = stock
    item, person = pending_booking(s, mixed=mixed)
    assert item.state == "confirmed"
    assert inventory.reserved(s.level) == int(mixed)
    inventory.ensure_stock(item)
    with pytest.raises(ValidationError, match="remaining participants"):
        inventory.consume(s.user, s.school.slug, item.public_id)
    original_total = item.order.total_cents
    item = save(s, item, person)
    assert not item.snapshot["workshop_selections_pending"]
    assert inventory.reserved(s.level) == 1 + int(mixed)
    assert item.order.total_cents == original_total
    assert item.order.entries.filter(kind="payment").count() == 1
    with pytest.raises(ValidationError, match="already been saved"):
        save(s, item, person)
    inventory.consume(s.user, s.school.slug, item.public_id)
    s.level.refresh_from_db()
    assert s.level.on_hand == 4 - int(mixed)


def test_existing_recipes_are_not_recalculated(stock):
    s = stock
    item, person = pending_booking(s, mixed=True)
    recipe = s.item.requirements.get()
    recipe.quantity = 2
    recipe.save()
    item = save(s, item, person)
    assert StockAllocation.objects.get(reservation=item).quantity == 3
    assert item.snapshot["materials"][0]["quantity"] == "3.000"


def test_inconsistent_pending_selections_are_rejected(stock):
    s = stock
    item, person = pending_booking(s)
    person.selections_deferred = False
    person.save()
    with pytest.raises(ValidationError, match="do not match"):
        inventory.validate_allocations(item, allow_pending=True)
    with pytest.raises(ValidationError, match="cannot also reserve"):
        inventory.selection_items(s.offering, [{"selections_deferred": True,
            "selections": [{"group_id": s.design.pk, "option_id": s.cat.pk}]}])


def test_stock_shortage_and_invalid_choices_do_not_mutate_booking(stock):
    s = stock
    item, person = pending_booking(s)
    for data in ({}, {f"choice_{s.design.pk}": "999999", f"choice_{s.color.pk}": str(s.blue.pk)}):
        with pytest.raises(ValidationError):
            save(s, item, person, data)
    s.level.on_hand = 0
    s.level.save()
    with pytest.raises(ValidationError, match="only 0"):
        save(s, item, person)
    person.refresh_from_db()
    assert person.selections_deferred and not person.selections
    assert not item.stock_allocations.exists()


@pytest.mark.parametrize("state,prepared", [("held", False), ("canceled", False), ("completed", False), ("confirmed", True)])
def test_closed_reservations_reject_changes(stock, state, prepared):
    s = stock
    item, person = pending_booking(s)
    item.state = state
    item.prepared_at = timezone.now() if prepared else None
    item.save()
    with pytest.raises(ValidationError, match="confirmed, unprepared"):
        save(s, item, person)


def test_staff_can_complete_on_event_day_even_if_setting_later_disabled(stock):
    s = stock
    item, person = pending_booking(s)
    item.starts_at = timezone.now() - timedelta(minutes=30)
    item.save()
    assert not s.offering.allow_deferred_selections
    save(s, item, person)


def test_at_studio_selection_cannot_use_unreceived_delivery(stock):
    from scheduling.models import IncomingDelivery
    s = stock
    item, person = pending_booking(s)
    item.starts_at = timezone.now() - timedelta(minutes=30)
    item.save()
    s.level.on_hand = 0
    s.level.save()
    IncomingDelivery.objects.create(level=s.level, quantity=5, arrives_on=timezone.now().date())
    with pytest.raises(ValidationError, match="only 0"):
        save(s, item, person)


def test_location_role_and_participant_boundaries(stock):
    s = stock
    item, person = pending_booking(s)
    other, other_person = pending_booking(s)
    with pytest.raises(Http404):
        save(s, other, person)
    member = SchedulingMembership.objects.get(user=s.user)
    member.role = "viewer"
    member.save()
    with pytest.raises(Http404):
        save(s, item, person)
    member.role, member.all_locations = "scheduler", False
    member.save()
    with pytest.raises(Http404):
        save(s, item, person)
    member.locations.add(s.location)
    save(s, item, person)
    other_person.refresh_from_db()
    assert other_person.selections_deferred


def test_staff_screen_action_and_error(stock):
    s = stock
    item, person = pending_booking(s)
    url = reverse("scheduling:reservation_detail", kwargs={"school_slug": s.school.slug, "public_id": item.public_id})
    response = s.client.get(url)
    assert response.status_code == 200
    action = f'edit_participant={person.pk}#participant-{person.pk}'.encode()
    assert action in response.content
    assert b'aria-label="Edit selections for ' in response.content
    assert b'title="Edit selections for ' in response.content
    assert b'<i data-lucide="pencil" aria-hidden="true"></i></a>' in response.content
    editing = s.client.get(url, {"edit_participant": person.pk})
    assert f'form="participant-form-{person.pk}"'.encode() in editing.content
    assert b'aria-label="Save selections for ' in editing.content
    assert b'<dialog id="participant-' not in editing.content
    assert b">Choose design</button>" not in response.content
    assert b"Choosing at the studio" in response.content
    assert s.client.post(url, {"action": "save_selections", "participant_id": "bad"}).status_code == 400
    response = s.client.post(url, {"action": "save_selections", "participant_id": person.pk})
    assert response.status_code == 400 and b"This field is required" in response.content
    response = s.client.post(url, {"action": "save_selections", "participant_id": person.pk, **choices(s)})
    assert response.status_code == 302
    response = s.client.get(url)
    assert action in response.content
    assert b">Choose design</button>" not in response.content
    assert b">Edit design</button>" not in response.content


@pytest.mark.parametrize("route", ["reservation_detail", "reservation_preparation"])
def test_inline_fields_save_errors_and_cancel_stay_on_current_tab(stock, route):
    s = stock
    item, person = pending_booking(s)
    url = reverse(f"scheduling:{route}", kwargs={"school_slug": s.school.slug, "public_id": item.public_id})
    edit_url = f"{url}?edit_participant={person.pk}"
    response = s.client.get(edit_url)
    assert response.context["inline_selection"]["person"].pk == person.pk
    assert response.context["participant_table"]["columns"] == ["Design", "Color", "Selection status"]
    assert response.context["selection_return_url"] == url + "#participants-title"
    assert f'form="participant-form-{person.pk}"'.encode() in response.content
    data = {"action": "save_selections", "participant_id": person.pk}
    response = s.client.post(edit_url, data)
    assert response.status_code == 400
    assert response.context["inline_selection"]["person"].pk == person.pk
    assert response.context["participant_table"]["rows"][0]["error"]
    response = s.client.post(edit_url, {**data, **choices(s)})
    assert response.status_code == 302 and response.url == url + "#participants-title"
    person.refresh_from_db()
    assert not person.selections_deferred
    s.client.get(edit_url)  # Opening or cancelling an editor never changes choices.
    assert inventory.reserved(s.level) == 1
    assert not s.client.get(url).context.get("inline_selection")
    assert not s.client.get(url, {"edit_participant": 999999}).context.get("inline_selection")
    member = SchedulingMembership.objects.get(user=s.user)
    member.role = "viewer"
    member.save()
    response = s.client.get(edit_url)
    assert not response.context.get("inline_selection")
    assert b'aria-label="Edit selections for ' not in response.content
    assert s.client.post(edit_url, {**data, **choices(s)}).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_reserves_the_last_material_only_once(stock):
    s = stock
    first, first_person = pending_booking(s)
    second, second_person = pending_booking(s)
    s.level.on_hand = 1
    s.level.save()

    def submit(pair):
        close_old_connections()
        try:
            save(s, *pair)
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [(first, first_person), (second, second_person)]))
    assert sorted(results) == [False, True]
    assert inventory.reserved(s.level) == 1
    assert StockAllocation.objects.count() == 1
