import uuid
from copy import deepcopy
from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from core.models import School
from core.payments.models import LedgerEntry, PaymentAttempt, PaymentAccount, Refund
from scheduling import booking_documents as documents, messaging, party_notifications, reservation_email
from scheduling.message_models import BookingMessage
from scheduling.models import SchedulingMembership
from scheduling.reservation_audit import audit_page
from scheduling.tests.test_inventory import stock, reserve
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email import data

pytestmark = pytest.mark.django_db


def entry(stock, item, amount=2500, kind="payment", **kwargs):
    return LedgerEntry.objects.create(order=item.order, school=stock.school, environment=item.order.environment,
        key=uuid.uuid4(), reference=str(uuid.uuid4()), kind=kind, amount_cents=amount, actor=stock.user, **kwargs)


def page(stock, item, kind="statement"):
    return reverse("scheduling:booking_document", args=[stock.school.slug, item.public_id, kind])


def test_party_statement_uses_saved_tax_and_ledger(stock, booking):
    booking.snapshot["party_package"] = {"name": "Birthday package", "subtotal": "90.00", "tax": "10.00"}
    booking.save()
    stock.location.default_tax_rate = 42
    stock.location.save()
    entry(stock, booking)
    entry(stock, booking, 1200, channel="cash", extra_charge_cents=200, paid_on=timezone.localdate(),
          external_reference="Cash receipt", note="Internal payment note must stay private")
    entry(stock, booking, 500, kind="refund")
    doc = documents.snapshot(booking, "receipt")
    assert doc["booking"] == "Birthday package"
    assert doc["lines"] == [
        {"label": "Subtotal", "amount": "$90.00"}, {"label": "Tax", "amount": "$10.00"},
        {"label": "Additional purchases", "amount": "$2.00"},
        {"label": "Total", "amount": "$102.00", "strong": True},
        {"label": "Payments received", "amount": "$37.00"}, {"label": "Refunds", "amount": "$5.00"},
        {"label": "Net received", "amount": "$32.00"},
        {"label": "Balance due", "amount": "$70.00", "strong": True}]
    assert "Internal payment note" not in str(doc)
    assert "Cash receipt" in str(doc["history"])
    assert not doc["tax_note"]
    response = stock.client.get(page(stock, booking, "receipt"))
    assert response.status_code == 200 and b"Download PDF" in response.content
    assert "no-store" in response["Cache-Control"]
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert b"Internal payment note" not in response.content


@pytest.mark.parametrize("state", ["confirmed", "completed", "canceled", "review"])
def test_receipt_records_received_money_not_booking_or_refund_promises(stock, booking, state):
    entry(stock, booking, 10000)
    if state == "canceled":
        entry(stock, booking, 10000, kind="refund")
    booking.state = state
    booking.save()
    doc = documents.snapshot(booking, "receipt")
    assert doc["status"] == booking.get_state_display()
    if state == "canceled":
        assert doc["lines"][-1]["amount"] == "Not due - canceled"
        assert "Canceled booking" in doc["note"]
        assert {"label": "Net received", "amount": "$0.00"} in doc["lines"]
    elif state == "review":
        assert "does not confirm" in doc["note"]
    else:
        assert doc["lines"][-1]["amount"] == "$0.00"


def test_unpaid_zero_and_pending_checkout_cannot_produce_receipt(stock):
    item = reserve(stock, total=10000)
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_doc", enabled=True)
    PaymentAttempt.objects.create(order=item.order, account=account, amount_cents=10000, state="open",
        expires_at=timezone.now(), success_url="https://example.invalid/", cancel_url="https://example.invalid/")
    with pytest.raises(ValidationError, match="after a payment"):
        documents.snapshot(item, "receipt")
    assert stock.client.get(page(stock, item, "receipt")).status_code == 404
    statement = documents.snapshot(item, "statement")
    assert statement["history"] == [] and "not proof" in statement["note"]
    item.order.total_cents = 0
    item.order.save()
    assert documents.snapshot(item, "statement")["lines"][-1]["amount"] == "$0.00"
    with pytest.raises(ValidationError):
        documents.snapshot(item, "receipt")


def test_voided_and_corrected_cash_entries_and_overpayment(stock, booking):
    original = entry(stock, booking, 10500, channel="cash", paid_on=timezone.localdate(), extra_charge_cents=500)
    entry(stock, booking, 10500, kind="void", channel="cash", paid_on=original.paid_on,
          reverses=original, extra_charge_cents=500)
    assert documents.snapshot(booking, "statement")["history"] == []
    with pytest.raises(ValidationError):
        documents.snapshot(booking, "receipt")
    entry(stock, booking, 11000)
    doc = documents.snapshot(booking, "receipt")
    assert len(doc["history"]) == 1
    assert doc["lines"][-1] == {"label": "Overpayment", "amount": "$10.00"}
    assert "Additional purchases" not in str(doc)


@pytest.mark.parametrize("state", ["queued", "pending", "unknown", "failed"])
def test_unverified_refund_is_not_reported_as_returned_money(stock, booking, state):
    paid = entry(stock, booking, 10000)
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_refund_doc", enabled=True)
    attempt = PaymentAttempt.objects.create(order=booking.order, account=account, amount_cents=10000,
        state="paid", entry=paid, expires_at=timezone.now(),
        success_url="https://example.invalid/", cancel_url="https://example.invalid/")
    Refund.objects.create(attempt=attempt, key=uuid.uuid4(), amount_cents=5000, reason="Requested refund",
        actor=stock.user, state=state)
    doc = documents.snapshot(booking, "receipt")
    assert {"label": "Payments received", "amount": "$100.00"} in doc["lines"]
    assert not any(line["label"] == "Refunds" for line in doc["lines"])
    assert len(doc["history"]) == 1


@pytest.mark.parametrize("quote", [{}, {"subtotal": "bad", "tax": "1"}, {"subtotal": "NaN", "tax": "1"},
    {"subtotal": "Infinity", "tax": "1"}, {"subtotal": "99", "tax": "2"},
    {"subtotal": "-1", "tax": "101"}, {"subtotal": "99.999", "tax": ".001"}])
def test_legacy_or_inconsistent_party_quote_never_invents_tax(stock, booking, quote):
    booking.snapshot["party_package"] = quote
    doc = documents.snapshot(booking, "statement")
    assert doc["lines"][0] == {"label": "Booked amount", "amount": "$100.00"}
    assert doc["tax_note"]


def test_workshop_saved_quote_timezone_and_escaped_customer_content(stock):
    item = reserve(stock, total=13000)
    source = item.workshop_request
    source.session_snapshot["price_breakdown"] = {"subtotal_cents": 12000, "tax_cents": 1000}
    source.purchaser_name = "Zoë <script>alert(1)</script>"
    source.save()
    item.snapshot["timezone"] = "America/Los_Angeles"
    item.save()
    doc = documents.snapshot(item, "statement")
    assert doc["lines"][1] == {"label": "Tax", "amount": "$10.00"}
    with timezone.override("Asia/Tokyo"):
        assert documents.snapshot(item, "statement")["when"] == doc["when"]
    response = stock.client.get(page(stock, item))
    assert b"<script>alert(1)" not in response.content and b"&lt;script&gt;" in response.content
    source.session_snapshot["price_breakdown"] = {"subtotal_cents": "12000", "tax_cents": 1000}
    source.save()
    assert documents.quote_breakdown(item) is None
    item.ends_at += timezone.timedelta(days=1)
    assert documents.snapshot(item, "statement")["when"].count(", 20") == 2


def test_documents_are_school_and_location_scoped_and_get_only(stock, booking, client):
    path = page(stock, booking)
    assert client.get(path).status_code == 302
    assert stock.client.post(path).status_code == 405
    other = School.objects.create(slug="other-doc-school")
    assert stock.client.get(path.replace(stock.school.slug, other.slug)).status_code == 404
    assert stock.client.get(page(stock, booking, "invoice")).status_code == 404
    SchedulingMembership.objects.filter(user=stock.user).update(all_locations=False)
    assert stock.client.get(path).status_code == 404
    assert stock.client.get(path + "?format=pdf").status_code == 404
    SchedulingMembership.objects.filter(user=stock.user).update(all_locations=True, role="viewer")
    assert stock.client.get(path).status_code == 200
    booking.order.school = other
    booking.order.save()
    assert stock.client.get(path).status_code == 404


def test_pdf_download_has_totals_unicode_pagination_and_no_private_notes(stock, booking):
    booking.party_request.organizer_name = "Zoë & José " + "Long customer name " * 4
    booking.party_request.save()
    for _ in range(65):
        entry(stock, booking, 100, channel="terminal", paid_on=timezone.localdate(), external_reference="T" * 100)
    response = stock.client.get(page(stock, booking, "receipt") + "?format=pdf")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert 'attachment; filename="receipt-' in response["Content-Disposition"]
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) > 2
    text = "\n".join(p.extract_text() for p in reader.pages)
    assert "Zoë & José" in text and "$65.00" in text and "$35.00" in text
    assert text.count("Square Terminal (staff-recorded)") == 65
    assert len(reader.pages) == text.count("Page ")
    assert "Payment history" in text


def test_document_options_show_in_both_workshop_and_party(stock, booking):
    booking.state = "canceled"
    booking.save()
    item = reserve(stock)
    for reservation in (item, booking):
        url = reverse("scheduling:reservation_detail", args=[stock.school.slug, reservation.public_id])
        response = stock.client.get(url)
        assert b'>Statement</a>' in response.content
        assert b'>Receipt</a>' not in response.content
        assert b'Booking statement (PDF)' in response.content
        entry(stock, reservation, 50)
        assert b'>Receipt</a>' in stock.client.get(url).content


def test_email_snapshot_is_immutable_idempotent_and_audited(stock, booking, settings):
    settings.DEBUG = True
    entry(stock, booking)
    payload = data(stock, booking, document="receipt")
    form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, payload)
    assert not form.errors
    snapshot = deepcopy(message.document_snapshot)
    entry(stock, booking, 2000)
    form, duplicate = reservation_email.send(stock.user, stock.school.slug, booking.public_id, payload)
    assert not form.errors and duplicate.pk == message.pk
    assert duplicate.document_snapshot == snapshot
    result = EmailMessage()
    documents.attach(result, duplicate)
    assert len(result.attachments) == 1
    assert result.attachments[0].mimetype == "application/pdf"
    content = PdfReader(BytesIO(result.attachments[0].content)).pages[0].extract_text()
    assert "$25.00" in content and "$75.00" in content and "$45.00" not in content
    assert any("Attachment: Payment receipt (PDF)" in row["detail"] for row in audit_page(booking, 1)["rows"])
    form, rejected = reservation_email.send(stock.user, stock.school.slug, booking.public_id, {**payload, "document": "statement"})
    assert form.errors and rejected is None and BookingMessage.objects.count() == 1
    form, rejected = reservation_email.send(stock.user, stock.school.slug, booking.public_id, {**payload, "document": ""})
    assert form.errors and rejected is None


def test_attachment_cannot_be_forged_or_generated_without_payment(stock, booking, settings):
    settings.DEBUG = True
    for document in ("receipt", "foreign-file", "../../private"):
        form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking, document=document))
        assert form.errors and message is None
    form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking, document="statement"))
    assert not form.errors
    message.document_snapshot["school_id"] += 1
    with pytest.raises(ValidationError):
        documents.attach(EmailMessage(), message)
    result = EmailMessage()
    message.document_snapshot = {}
    documents.attach(result, message)
    assert result.attachments == []


def test_email_attachment_uses_existing_school_transport_and_retries(stock, booking, settings, monkeypatch):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    booking.order.environment = "live"
    booking.order.save()
    sent = []
    class Transport:
        def send_messages(self, emails):
            sent.extend(emails)
            return 0 if len(sent) == 1 else 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    payload = data(stock, booking, document="statement")
    form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, payload)
    assert not form.errors
    assert party_notifications.process(stock.profile.pk) == 1
    message.refresh_from_db()
    assert message.state == "retry"
    frozen = deepcopy(message.document_snapshot)
    BookingMessage.objects.filter(pk=message.pk).update(next_attempt_at=timezone.now())
    assert party_notifications.process(stock.profile.pk) == 1
    message.refresh_from_db()
    assert message.state == "sent" and message.document_snapshot == frozen
    for email in sent:
        assert len(email.attachments) == 1 and email.attachments[0].filename.startswith("statement-")
    assert party_notifications.process(stock.profile.pk) == 0


def test_local_transport_includes_pdf_without_external_send(stock, booking, settings, mailoutbox):
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    stock.profile.message_preview_enabled = True
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking, document="statement"))
    assert not form.errors
    BookingMessage.objects.filter(pk=message.pk).update(state="queued")
    assert messaging.process_batch(stock.profile.pk) == 1
    assert len(mailoutbox) == 1 and len(mailoutbox[0].attachments) == 1


@pytest.mark.django_db(transaction=True)
def test_document_snapshot_migration_preserves_existing_outbox(stock, booking, settings):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    settings.DEBUG = True
    form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking))
    assert not form.errors
    original = message.payload_digest
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("scheduling", "0052_square_multiple_accounts")])
        MigrationExecutor(connection).migrate(latest)
        saved = BookingMessage.objects.get(pk=message.pk)
        assert saved.payload_digest == original and saved.document_snapshot == {}
        assert saved.reservation_id == booking.pk and saved.body == message.body
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_concurrent_document_send_creates_one_frozen_attachment(stock, booking, settings):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    settings.DEBUG = True
    entry(stock, booking)
    payload = data(stock, booking, document="receipt")
    def send():
        close_old_connections()
        try:
            form, message = reservation_email.send(stock.user, stock.school.slug, booking.public_id, payload)
            assert not form.errors
            return message.pk, message.document_snapshot
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: send(), range(2)))
    assert first == second
    assert BookingMessage.objects.count() == 1
