"""Customer-facing statements from saved quotes and the payment ledger only."""
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.payments.service import totals

KINDS = {"statement": "Booking statement", "receipt": "Payment receipt"}


def money(cents):
    return f"${Decimal(cents) / 100:,.2f}"


def quote_breakdown(item):
    """Never infer an older quote's tax using the studio's current rate."""
    if item.party_request_id:
        quote = item.snapshot.get("party_package", {})
        try:
            subtotal = Decimal(quote["subtotal"]) * 100
            tax = Decimal(quote["tax"]) * 100
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None
    else:
        quote = item.workshop_request.session_snapshot.get("price_breakdown", {})
        subtotal, tax = quote.get("subtotal_cents"), quote.get("tax_cents")
        if type(subtotal) is not int or type(tax) is not int:
            return None
    if (not Decimal(subtotal).is_finite() or not Decimal(tax).is_finite()
            or subtotal < 0 or tax < 0 or subtotal != int(subtotal) or tax != int(tax)
            or subtotal + tax != item.order.total_cents):
        return None
    return int(subtotal), int(tax)


def snapshot(item, kind):
    if kind not in KINDS or not (item.party_request_id or item.workshop_request_id):
        raise ValidationError("Choose a workshop or party booking document.")
    if item.order.school_id != item.profile.school_id:
        raise ValidationError("This payment record does not belong to this studio.")
    amounts = totals(item.order)
    if kind == "receipt" and not amounts["paid_cents"]:
        raise ValidationError("A receipt is available after a payment has been recorded.")
    source = item.party_request if item.party_request_id else item.workshop_request
    location = source.offering.location if item.party_request_id else source.session.offering.location
    zone = ZoneInfo(item.snapshot.get("timezone") or location.timezone_name)
    start, end = timezone.localtime(item.starts_at, zone), timezone.localtime(item.ends_at, zone)
    generated = timezone.localtime(timezone.now(), zone)
    breakdown = quote_breakdown(item)
    lines = ([{"label": "Subtotal", "amount": money(breakdown[0])},
              {"label": "Tax", "amount": money(breakdown[1])}] if breakdown else
             [{"label": "Booked amount", "amount": money(item.order.total_cents)}])
    if amounts["extras_cents"]:
        lines.append({"label": "Additional purchases", "amount": money(amounts["extras_cents"])})
    lines.extend([{"label": "Total", "amount": money(amounts["total_cents"]), "strong": True},
                  {"label": "Payments received", "amount": money(amounts["paid_cents"])}])
    if amounts["refunded_cents"]:
        lines.extend([{"label": "Refunds", "amount": money(amounts["refunded_cents"])},
                      {"label": "Net received", "amount": money(amounts["net_cents"])}])
    canceled = item.state == "canceled"
    lines.append({"label": "Balance due", "amount": "Not due - canceled" if canceled else money(amounts["outstanding_cents"]), "strong": True})
    if amounts["net_cents"] > amounts["total_cents"]:
        lines.append({"label": "Overpayment", "amount": money(amounts["net_cents"] - amounts["total_cents"])})
    entries = item.order.entries.exclude(kind="void").filter(reversal__isnull=True).order_by("created_at", "pk")
    history = [{"date": (entry.paid_on or timezone.localtime(entry.created_at, zone).date()).strftime("%b %d, %Y"),
                "type": entry.get_kind_display(), "method": entry.get_channel_display(),
                "reference": entry.external_reference if entry.channel in {"terminal", "cash"} else "",
                "amount": money(entry.amount_cents)} for entry in entries]
    return {"version": 1, "kind": kind, "title": KINDS[kind],
            "school_id": item.profile.school_id, "reservation_id": str(item.public_id),
            "business": item.profile.school.display_name or item.profile.school.slug,
            "reference": str(item.public_id), "generated": generated.strftime("%b %d, %Y at %I:%M %p %Z"),
            "customer": source.organizer_name if item.party_request_id else source.purchaser_name,
            "email": source.organizer_email if item.party_request_id else source.purchaser_email,
            "booking": item.snapshot.get("party_package", {}).get("name") or item.snapshot.get("workshop", "Workshop"),
            "booking_kind": "Party" if item.party_request_id else "Workshop", "status": item.get_state_display(),
            "location": location.name, "address": location.address,
            "when": f"{start:%a, %b %d, %Y at %I:%M %p} - {end:%I:%M %p %Z}" if start.date() == end.date()
                    else f"{start:%a, %b %d, %Y at %I:%M %p} - {end:%a, %b %d, %Y at %I:%M %p %Z}",
            "quantity": item.quantity, "lines": lines, "history": history,
            "note": ("Canceled booking. Any refund is shown separately when recorded." if canceled else
                     "Payment received does not confirm this booking; contact the studio." if item.state == "review" else
                     "This statement is not proof of payment." if kind == "statement" else ""),
            "tax_note": "" if breakdown else "A separate tax breakdown was not recorded for this booking."}


def filename(document):
    return f'{document["kind"]}-{document["reference"]}.pdf'


def attach(email, message):
    document = message.document_snapshot
    if not document:
        return
    if (not message.reservation_id or document.get("school_id") != message.profile.school_id
            or document.get("reservation_id") != str(message.reservation.public_id)):
        raise ValidationError("The attached document does not belong to this reservation.")
    from .booking_document_pdf import render_pdf
    email.attach(filename(document), render_pdf(document), "application/pdf")
