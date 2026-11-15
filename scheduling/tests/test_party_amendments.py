"""Paid-party edits use the shared editor and keep payment evidence immutable."""
from datetime import timedelta
from decimal import Decimal
import uuid
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments.models import PaymentAccount, PaymentAttempt, Refund
from core.payments.service import record_offline, totals
from scheduling import booking_edits, booking_documents, guest_lists, reservation_email
from scheduling.models import PartyPackage, SchedulingMembership
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_guest_lists import booking, payload, url as guest_url
from scheduling.tests.test_booking_edits import edit_data, save

pytestmark = pytest.mark.django_db


@pytest.fixture
def party(stock, booking):
    stock.session.delete()
    package = PartyPackage.objects.create(offering=stock.offering, name="Small party", participant_limit=2,
        additional_participant_limit=4, additional_participant_price=25, duration_minutes=120,
        total=100, deposit=50, tax_rate=10, room=stock.room, space="exclusive", is_published=True)
    source = booking.party_request
    source.package, source.package_snapshot = package, package.quote(2)
    source.proposed_total, source.proposed_deposit = 110, 50
    source.save()
    booking.snapshot.update(party_package=source.package_snapshot, space="exclusive")
    booking.exclusive = True
    booking.save()
    booking.order.total_cents, booking.order.deposit_cents = 11000, 5000
    booking.order.merchant_reference = f"offline:{stock.school.pk}"
    booking.order.description = "Synthetic party"
    booking.order.save()
    record_offline(order=booking.order, actor=stock.user, key=uuid.uuid4(), kind="payment",
        amount_cents=5000, reference="Synthetic deposit")
    booking.refresh_from_db()
    return booking


def body(s, item, **changes):
    item.refresh_from_db()
    return edit_data(s, item, **{"starts_at": timezone.localtime(item.starts_at,
        ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M"),
        "package": str(item.party_request.package_id or ""), **changes})


def confirm(s, item, values):
    saved, form, preview = save(s, item, values)
    assert saved is None and not form.errors and preview, form.errors
    values["review_token"] = form.data["review_token"]
    saved, form, again = save(s, item, values)
    assert not form.errors and saved and again is None, form.errors
    return saved, preview


def alternative(s, **changes):
    values = dict(offering=s.offering, name="Large party", participant_limit=6, total=200,
        deposit=100, tax_rate=10, room=s.room, duration_minutes=180, space="exclusive", is_published=True)
    return PartyPackage.objects.create(**{**values, **changes})


def test_upgrade_preview_retry_and_statement_preserve_payment(stock, party):
    package = alternative(stock)
    evidence = list(party.order.entries.values())
    values = body(stock, party)
    values.update(package=str(package.pk), quantity="4", price_adjustment="-10", adjustment_note="Agreed phone price")
    saved, form, preview = save(stock, party, values)
    assert saved is None and preview["total"] == 209 and preview["balance"] == 159
    party.refresh_from_db(); party.order.refresh_from_db()
    assert party.order.total_cents == 11000 and party.quantity == 2
    assert party.party_request.package.name == "Small party"
    values["review_token"] = form.data["review_token"]
    saved, form, preview = save(stock, party, values)
    assert saved.public_id == party.public_id and saved.order_id == party.order_id
    assert saved.order.deposit_cents == 5000 and saved.quantity == 4
    assert saved.ends_at - saved.starts_at == timedelta(hours=3)
    assert saved.party_request.package_id == package.pk
    assert saved.party_request.proposed_total == Decimal("209")
    assert list(saved.order.entries.values()) == evidence
    assert save(stock, saved, values)[0].pk == saved.pk
    assert AdminAuditLog.objects.filter(changes__action="staff_booking_updated").count() == 1
    audit = AdminAuditLog.objects.get(changes__action="staff_booking_updated")
    assert audit.extra["adjustment_note"] == "Agreed phone price"
    document = booking_documents.snapshot(saved, "statement")
    assert document["booking"] == "Large party" and document["quantity"] == 4
    assert {r["label"]: r["amount"] for r in document["lines"]}["Balance due"] == "$159.00"


def test_same_package_retains_agreed_prices_and_cumulative_adjustment(stock, party):
    PartyPackage.objects.filter(pk=party.party_request.package_id).update(total=900, tax_rate=20, deposit=300)
    saved, preview = confirm(stock, party, body(stock, party, quantity="3", price_adjustment="-5"))
    assert saved.order.total_cents == 13200 and preview["balance"] == 82
    saved, _ = confirm(stock, saved, body(stock, saved, quantity="4"))
    assert saved.order.total_cents == 15950
    assert booking_documents.quote_breakdown(saved) == (14500, 1450)
    assert saved.order.deposit_cents == 5000


def test_count_within_included_allowance_still_requires_review(stock, party):
    values = body(stock, party, quantity="1")
    saved, preview = confirm(stock, party, values)
    assert saved.quantity == 1 and preview["total"] == 110


@pytest.mark.parametrize("adjustment,changed,excess", [("", False, False), ("10", True, False), ("-80", True, True)])
def test_review_is_compact_but_keeps_breakdown_and_excess_visible(stock, party, adjustment, changed, excess):
    path = url(stock, "staff_booking_edit", public_id=party.public_id)
    response = stock.client.post(path, body(stock, party, quantity="1", price_adjustment=adjustment))
    assert response.status_code == 400 and not response.context["form"].errors
    html = response.content.decode()
    assert '<dl class="booking-review-totals" role="status">' in html
    assert '<details class="booking-review-breakdown"><summary>Price breakdown</summary>' in html
    assert ("Previous total" in html) == changed
    assert ("No refund will be issued by saving." in html) == excess
    assert "Updated time:" not in html and "Updated room:" not in html
    assert "&rarr;" not in html and "Refunded" not in html
    assert "Payments received" in html and "Tax" in html
    party.order.refresh_from_db()
    assert party.order.total_cents == 11000


@pytest.mark.parametrize("base,rate,adjustment,expected_tax,expected_total", [
    ("895", "11", "-10", "97.35", "982.35"),
    ("100", "8.87", "-0.01", "8.87", "108.86"),
    ("1", "0.5", "", "0.01", "1.01"),
])
def test_amended_quote_rounding_and_paid_balance(stock, party, base, rate, adjustment, expected_tax, expected_total):
    package = alternative(stock, total=Decimal(base), tax_rate=Decimal(rate), deposit=0)
    values = body(stock, party, package=str(package.pk), quantity="4", price_adjustment=adjustment)
    path = url(stock, "staff_booking_edit", public_id=party.public_id)
    response = stock.client.post(path, values)
    assert not response.context["form"].errors
    assert b"Updated time:" in response.content
    preview = response.context["preview"]
    assert Decimal(preview["party"]["quote"]["tax"]) == Decimal(expected_tax)
    assert preview["total"] == Decimal(expected_total)
    assert preview["balance"] == max(Decimal("0"), Decimal(expected_total) - 50)
    assert preview["excess"] == max(Decimal("0"), 50 - Decimal(expected_total))
    saved, preview = confirm(stock, party, values)
    assert totals(saved.order)["total"] == Decimal(expected_total)
    assert totals(saved.order)["paid"] == 50
    assert totals(saved.order)["refunded"] == 0


def test_downgrade_excess_and_deposit_clamp_do_not_refund(stock, party):
    saved, preview = confirm(stock, party, body(stock, party, price_adjustment="-80"))
    assert saved.order.total_cents == 2200 and saved.order.deposit_cents == 2200
    assert preview["excess"] == 28 and preview["balance"] == 0
    assert totals(saved.order)["net_cents"] == 5000
    assert not Refund.objects.exists() and not saved.order.entries.filter(kind="refund").exists()
    assert booking_documents.snapshot(saved, "receipt")["lines"][-1] == {"label": "Overpayment", "amount": "$28.00"}


def test_guest_reduction_preserves_survivor_selection_recipe_and_preparation(stock, party):
    assert stock.client.post(guest_url(party), payload(stock)).status_code == 302
    party.refresh_from_db(); party.party_request.refresh_from_db()
    from scheduling.lessons import digest
    party.snapshot["participant_preparation"] = {"1": digest(stock.item.pk)}
    party.save()
    stock.item.requirements.update(quantity=3)
    values = body(stock, party, quantity="1")
    saved, form, _ = save(stock, party, values)
    assert saved is None and "remove" in form.errors
    values["remove"] = "0"
    saved, _ = confirm(stock, party, values)
    assert guest_lists.saved_rows(saved)[0]["guest_name"] == "Guest 2"
    assert saved.stock_allocations.get().quantity == 1
    assert saved.snapshot["participant_preparation"] == {"0": digest(stock.item.pk)}
    assert not saved.snapshot["guest_selections_pending"]
    saved, _ = confirm(stock, saved, body(stock, saved, quantity="3"))
    assert guest_lists.saved_rows(saved)[0]["guest_name"] == "Guest 2"
    assert saved.stock_allocations.get().quantity == 1 and saved.snapshot["guest_selections_pending"]


@pytest.mark.parametrize("field,value", [("quantity", "0"), ("quantity", "7"), ("price_adjustment", "NaN"),
    ("price_adjustment", "1.001"), ("price_adjustment", "-101"), ("package", "999999"), ("package", "")])
def test_invalid_changes_never_write(stock, party, field, value):
    before = booking_edits.evidence(party)
    try:
        saved, form, _ = save(stock, party, body(stock, party, **{field: value}))
        assert saved is None and form.errors
    except ValidationError:
        pass
    party.refresh_from_db()
    assert booking_edits.evidence(party) == before


def test_capacity_conflict_prevents_even_price_preview(stock, party):
    stock.room.capacity = 2; stock.room.save()
    with pytest.raises(ValidationError, match="capacity"):
        save(stock, party, body(stock, party, quantity="3"))
    assert party.order.total_cents == 11000


def test_package_duration_conflict_is_checked(stock, party):
    from scheduling.models import Session
    package = alternative(stock)
    Session.objects.create(offering=stock.offering, room=stock.room, capacity=5, is_published=True,
        starts_at=party.ends_at + timedelta(minutes=15), ends_at=party.ends_at + timedelta(hours=2))
    values = body(stock, party); values["package"] = str(package.pk)
    with pytest.raises(ValidationError, match="published session"):
        save(stock, party, values)


def test_stale_package_payment_and_guest_changes_invalidate_review(stock, party):
    values = body(stock, party, price_adjustment="10")
    _, form, _ = save(stock, party, values)
    values["review_token"] = form.data["review_token"]
    PartyPackage.objects.filter(pk=party.party_request.package_id).update(total=120)
    with pytest.raises(ValidationError, match="settings changed"):
        save(stock, party, values)
    values = body(stock, party, quantity="3")
    record_offline(order=party.order, actor=stock.user, key=uuid.uuid4(), kind="payment", amount_cents=100, reference="Extra")
    with pytest.raises(ValidationError, match="booking changed"):
        save(stock, party, values)
    values = body(stock, party, quantity="3")
    stock.client.post(guest_url(party), payload(stock))
    with pytest.raises(ValidationError, match="booking changed"):
        save(stock, party, values)


def test_incomplete_saved_quote_blocks_pricing_not_contacts(stock, party):
    party.party_request.package_snapshot = {}; party.party_request.save()
    with pytest.raises(ValidationError, match="incomplete"):
        save(stock, party, body(stock, party, quantity="3"))
    saved, form, _ = save(stock, party, body(stock, party, purchaser_name="New host"))
    assert saved.party_request.organizer_name == "New host" and not form.errors


def test_unpublished_current_package_is_retained_but_not_offered_as_new(stock, party):
    PartyPackage.objects.filter(pk=party.party_request.package_id).update(is_published=False)
    hidden = alternative(stock, is_published=False)
    form = booking_edits.BookingEditForm(item=party, user=stock.user)
    assert set(form.fields["package"].queryset.values_list("pk", flat=True)) == {party.party_request.package_id}
    values = body(stock, party); values["package"] = str(hidden.pk)
    assert "package" in save(stock, party, values)[1].errors
    assert confirm(stock, party, body(stock, party, quantity="3"))[0].quantity == 3


def test_custom_party_does_not_infer_per_person_price_or_tax(stock, booking):
    stock.session.delete()
    booking.order.description = "Custom party"
    booking.order.merchant_reference = f"offline:{stock.school.pk}"
    booking.order.save()
    saved, preview = confirm(stock, booking, body(stock, booking, quantity="4", price_adjustment="-10"))
    assert saved.order.total_cents == 9000 and preview["total"] == 90
    assert booking_documents.quote_breakdown(saved) is None


@pytest.mark.parametrize("pending_refund", [False, True])
def test_unsettled_money_blocks_changes(stock, party, pending_refund):
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_party_edit")
    attempt = PaymentAttempt.objects.create(order=party.order, account=account, amount_cents=5000,
        state="paid" if pending_refund else "unknown", expires_at=timezone.now()+timedelta(hours=1))
    if pending_refund:
        Refund.objects.create(attempt=attempt, key=uuid.uuid4(), amount_cents=100, actor=stock.user, reason="Test")
    with pytest.raises(ValidationError, match="still processing"):
        save(stock, party, body(stock, party, quantity="3"))


def test_permissions_and_location_scope(stock, party):
    values = body(stock, party, quantity="3")
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    with pytest.raises(Http404):
        save(stock, party, values)
    assert stock.client.post(url(stock, "staff_booking_edit", public_id=party.public_id), values).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_amendments_only_one_wins(stock, party):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    barrier = Barrier(2)
    submissions = []
    for amount in ("10", "20"):
        values = body(stock, party, price_adjustment=amount)
        _, form, _ = save(stock, party, values)
        values["review_token"] = form.data["review_token"]
        submissions.append(values)
    def submit(values):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return bool(save(stock, party, values)[0])
        except ValidationError:
            return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, submissions)) == [False, True]
    assert AdminAuditLog.objects.filter(changes__action="staff_booking_updated").count() == 1


def test_updated_email_uses_existing_statement_attachment(stock, party, settings, mailoutbox):
    from scheduling.tests.test_reservation_email import data
    settings.DEBUG = True
    saved, _ = confirm(stock, party, body(stock, party, quantity="3"))
    form, message = reservation_email.send(stock.user, stock.school.slug, saved.public_id,
        data(stock, saved, document="statement", subject="Updated party booking"))
    assert not form.errors and message.document_snapshot["quantity"] == 3
    lines = {r["label"]: r["amount"] for r in message.document_snapshot["lines"]}
    assert lines["Total"] == "$137.50" and lines["Balance due"] == "$87.50"
    assert not mailoutbox


def test_payment_link_rejects_old_amount_and_new_email_has_updated_balance(stock, party, settings):
    from scheduling.tests.test_reservation_email import data, template
    settings.DEBUG = True
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_party_test", enabled=True)
    party.order.account = account
    party.order.merchant_reference = account.reference
    party.order.save()
    saved_template = template(stock, booking_link="payment")
    old_link = reservation_email.context_for(stock.user, party)["email_options"][str(saved_template.pk)]["link_token"]
    saved, _ = confirm(stock, party, body(stock, party, quantity="3"))
    form, message = reservation_email.send(stock.user, stock.school.slug, saved.public_id,
        data(stock, saved, template=str(saved_template.pk), link_token=old_link))
    assert message is None and "amount changed" in str(form.errors)
    link = reservation_email.context_for(stock.user, saved)["email_options"][str(saved_template.pk)]["link_token"]
    form, message = reservation_email.send(stock.user, stock.school.slug, saved.public_id,
        data(stock, saved, template=str(saved_template.pk), link_token=link, document="statement"))
    assert not form.errors and "Pay $87.50" in message.body
    assert message.notice_kind == "manual_payment"


def test_existing_credit_and_refund_are_not_baked_into_new_quote(stock, party):
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_party_test")
    attempt = PaymentAttempt.objects.create(order=party.order, account=account, amount_cents=5000,
        state="paid", expires_at=timezone.now(), collection_method="terminal", device_reference="test",
        requested_by=stock.user, adjustment_kind="credit", adjustment_cents=-1000)
    refund_entry = record_offline(order=party.order, actor=stock.user, key=uuid.uuid4(),
        kind="refund", amount_cents=500, reference="Synthetic refund")
    Refund.objects.create(attempt=attempt, key=uuid.uuid4(), amount_cents=500, reduction_cents=200,
        entry=refund_entry, state="succeeded", actor=stock.user, reason="Synthetic partial refund")
    evidence = list(party.order.entries.values())
    saved, preview = confirm(stock, party, body(stock, party, quantity="3"))
    assert saved.order.total_cents == 13750
    assert preview["total"] == Decimal("125.50") and preview["balance"] == 80.5
    assert preview["refunded"] == 5 and list(saved.order.entries.values()) == evidence
    assert preview["money"]["credit"] == 10 and preview["money"]["refund_reduction"] == 2
    assert preview["base_total"] - preview["money"]["credit"] - preview["money"]["refund_reduction"] == preview["total"]
    assert preview["total"] - (preview["paid"] - preview["refunded"]) == preview["balance"]
    with pytest.raises(ValidationError, match="discounts or credits"):
        save(stock, saved, body(stock, saved, price_adjustment="-125"))


def test_foreign_package_and_revoked_location_are_rejected(stock, party):
    from scheduling.models import Location, Offering
    other_location = Location.objects.create(profile=stock.profile, name="Elsewhere")
    other_offering = Offering.objects.create(location=other_location, name="Other party")
    other = alternative(stock, offering=other_offering)
    values = body(stock, party, package=str(other.pk))
    assert "package" in save(stock, party, values)[1].errors
    membership = SchedulingMembership.objects.get(user=stock.user)
    membership.all_locations = False
    membership.save()
    membership.locations.add(other_location)
    with pytest.raises(Http404):
        save(stock, party, body(stock, party, quantity="3"))


def test_changing_review_values_requires_another_review(stock, party):
    values = body(stock, party, price_adjustment="10")
    _, form, _ = save(stock, party, values)
    values["review_token"] = form.data["review_token"]
    values["price_adjustment"] = "20"
    saved, form, preview = save(stock, party, values)
    assert saved is None and preview["total"] == 132
    party.order.refresh_from_db()
    assert party.order.total_cents == 11000
