from collections import Counter
from datetime import timedelta, timezone as utc_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import uuid

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from scheduling import requests, reservations
from scheduling.booking_policies import notice
from scheduling.models import Reservation, SchedulingMembership, SchedulingProfile, Session, WorkshopRequest
from scheduling.integrations.payloads import BookingSnapshot, Participant, NotionDestination
from scheduling.request_forms import request_forms
from .config import available
from scheduling.preparation import form_for

REVIEW_SALT = "scheduling.experience-review.v1"
EXAMPLE_DATA_SOURCE = "00000000-0000-4000-8000-000000000001"


class ReviewForm(forms.Form):
    policy_ack = forms.BooleanField(label="I have reviewed the workshop and cancellation information.")


def money(cents):
    return Decimal(cents) / 100


def booking_cutoff(*, online=False):
    from scheduling.checkout import CHECKOUT_LEAD_TIME
    return timezone.now() + (CHECKOUT_LEAD_TIME if online else timedelta())


def sessions(profile, *, online=False):
    return Session.objects.filter(offering__location__profile=profile,
        offering__is_published=True, offering__visit_program__isnull=True, is_published=True,
        starts_at__gt=booking_cutoff(online=online), camps__isnull=True).select_related("offering__location__profile", "room").prefetch_related("camps").order_by("starts_at", "pk")


def session_rows(profile, *, online=False):
    result = []
    catalogs = {}
    for session in sessions(profile, online=online)[:240]:
        key = ("catalog", session.offering.inventory_catalog_id) if session.offering.inventory_catalog_id else ("workshop", session.offering_id)
        if key not in catalogs:
            catalogs[key] = requests.catalog_for(session.offering)
        if not requests.accepts_reservations(session, catalogs[key]):
            continue
        location = session.offering.location
        local = timezone.localtime(session.starts_at, ZoneInfo(location.timezone_name))
        result.append({"id": session.pk, "location_id": location.pk, "location": location.name,
            "workshop_id": session.offering_id, "workshop": session.offering.name,
            "duration_minutes": round((session.ends_at-session.starts_at).total_seconds() / 60),
            "date": local.strftime("%Y-%m-%d"),
            "day": local.strftime("%a, %b %d"), "time": local.strftime("%I:%M %p"),
            "end": timezone.localtime(session.ends_at, ZoneInfo(location.timezone_name)).strftime("%I:%M %p"),
            "places": max(0, session.capacity-reservations.session_places_taken(session))})
    return result


def payload(data, catalog):
    # Only form fields enter the signed review. Browser-supplied prices and actions never do.
    allowed = {"request_token", "purchaser_name", "purchaser_email", "policy_ack", "choose_when",
               "participants-TOTAL_FORMS", "participants-INITIAL_FORMS"}
    for index in range(21):
        allowed.add(f"participants-{index}-name")
        allowed.add(f"participants-{index}-selections_deferred")
        allowed.update(f"participants-{index}-choice_{group.pk}" for group in catalog)
    result = QueryDict(mutable=True)
    for key in allowed & data.keys():
        result.setlist(key, data.getlist(key))
    return result


def validate(session, data, *, check_stock=True):
    catalog = requests.catalog_for(session.offering)
    purchaser, participants = request_forms(session, catalog, data)
    valid = purchaser.is_valid()
    valid = participants.is_valid() and valid
    policy = ReviewForm(data)
    valid = policy.is_valid() and valid
    if valid and check_stock:
        from scheduling.inventory import check_selections
        try:
            check_selections(session.offering, [f.participant_snapshot() for f in participants], starts_at=session.starts_at)
        except ValidationError as exc:
            purchaser.add_error(None, exc)
            valid = False
    return catalog, purchaser, participants, policy, valid


def review_token(session, data, catalog, browser, config):
    return signing.dumps({"session": session.pk, "browser": browser,
        "digest": requests.submission_digest(payload(data, catalog)), "price": config["price_cents"],
        "deposit": str(session.offering.deposit_per_participant),
        "policy": config["policy_revision"], "published_policy": notice(session.offering.location.profile.school, "workshop"),
        "stripe_checkout": bool(config.get("stripe_checkout")),
        "payment_provider": config.get("payment_provider", "stripe")}, salt=REVIEW_SALT)


def actor(profile, config):
    member = get_object_or_404(SchedulingMembership.objects.select_related("user"), profile=profile,
        user__username=config["owner"], user__is_active=True, user__is_superuser=False, is_active=True, role="owner")
    return member.user


@transaction.atomic
def begin(profile, session, data, browser, config):
    if not available(profile, require_intake=True):
        raise Http404
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile.refresh_from_db()
    if not available(profile, require_intake=True):
        raise Http404
    session.offering.refresh_from_db()
    if session.starts_at <= booking_cutoff(online=bool(config.get("stripe_checkout"))):
        raise ValidationError("Online booking has closed for this session. Choose another time.")
    catalog = requests.catalog_for(session.offering)
    body = payload(data, catalog)
    try:
        token = signing.loads(data.get("review_token", ""), salt=REVIEW_SALT, max_age=900)
        if (token["session"] != session.pk or not browser or not constant_time_compare(token["browser"], browser)
                or token["digest"] != requests.submission_digest(body)
                or token["price"] != config["price_cents"] or token["policy"] != config["policy_revision"]
                or token.get("published_policy", {}) != notice(profile.school, "workshop")
                or token.get("deposit") != str(session.offering.deposit_per_participant)
                or token.get("stripe_checkout", False) != bool(config.get("stripe_checkout"))
                or token.get("payment_provider", "stripe") != profile.payment_provider):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("Your review expired or changed. Review the booking again before continuing.") from None
    if not ReviewForm(body).is_valid():
        raise ValidationError("Review the example policies before continuing.")
    item, purchaser, participants = requests.submit_request(profile.school.slug, session.pk, body, browser, for_reservation=True)
    if item is None:
        raise ValidationError("Some participant details are incomplete. Return to your selections.")
    if "experience_demo" not in item.session_snapshot:
        item.session_snapshot["experience_demo"] = {"policy": config["policy_revision"],
            "published_policy": notice(profile.school, "workshop"),
            "reviewed_at": timezone.now().isoformat(), "price_cents": config["price_cents"]}
        item.save(update_fields=["session_snapshot"])
    total, deposit = reservations.workshop_quote(session.offering, config["price_cents"], item.participants.count())
    reservations.create_reservation(actor(profile, config), profile.school.slug, "workshop", item.public_id,
                                   total_cents=total, deposit_cents=deposit, online=bool(config.get("stripe_checkout")))
    return item


def customer_booking(profile, public_id, browser):
    if not browser:
        raise Http404
    return get_object_or_404(WorkshopRequest.objects.select_related("session__offering__location", "reservation__order")
        .prefetch_related("participants"), session__offering__location__profile=profile,
        session_snapshot__has_key="experience_demo", public_id=public_id, browser_digest=browser)


@transaction.atomic
def simulate(profile, item, config, action):
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile.refresh_from_db()
    if not available(profile):
        raise Http404
    reservation = Reservation.objects.select_related("order").get(workshop_request=item, profile=profile)
    if reservation.order.account_id:
        raise ValidationError("Stripe bookings require a verified Stripe payment, not a simulated result.")
    user = actor(profile, config)
    if action == "complete":
        if reservation.state == "canceled":
            raise ValidationError("This booking was canceled. Start a new booking.")
        if reservation.state not in ("confirmed", "completed"):
            reservations.ensure_capacity(reservation)
        return reservations.settle_offline(user, profile.school.slug, reservation.public_id,
            key=uuid.uuid5(item.public_id, "local-demo-success"), kind="payment",
            amount_cents=reservation.order.deposit_cents or reservation.order.total_cents, reference=f"DEMO-NO-MONEY-{item.public_id}")
    if action == "abandon":
        if reservation.state in ("confirmed", "completed"):
            raise ValidationError("A confirmed booking cannot be abandoned from checkout.")
        return reservations.cancel_reservation(user, profile.school.slug, reservation.public_id)
    raise ValidationError("Choose a supported demo action.")


def has_simulated_payment(profile, reservation):
    """Only the exact local demo receipt is eligible for a simulated refund."""
    if not available(profile) or reservation.order.account_id or not reservation.workshop_request_id:
        return False
    source = reservation.workshop_request
    return bool("experience_demo" in source.session_snapshot
        and reservation.order.entries.filter(kind="payment").count() == 1
        and reservation.order.entries.filter(kind="payment", channel="offline",
            key=uuid.uuid5(source.public_id, "local-demo-success"),
            reference=f"DEMO-NO-MONEY-{source.public_id}",
            amount_cents=reservation.order.deposit_cents or reservation.order.total_cents).exists())


def refund_simulated_payment(user, profile, reservation, key):
    from core.payments import service as payments
    if reservation.state != "canceled" or not has_simulated_payment(profile, reservation):
        raise ValidationError("Only canceled local demo bookings can receive a simulated refund.")
    return payments.record_offline(order=reservation.order, actor=user,
        key=uuid.uuid5(key, "local-demo-refund"), kind="refund",
        amount_cents=payments.totals(reservation.order)["net_cents"],
        reference=f"DEMO-NO-MONEY-REFUND-{reservation.public_id}")


def selection_previews(rows, offering, config):
    """Resolve images once per page, scoped to this workshop's catalog."""
    variants = list(offering.inventory_catalog.items.all()) if offering.inventory_catalog_id else []
    for row in rows:
        choices = row["selections"]
        selected = {choice.get("option_id") for choice in choices}
        design = next((choice for choice in choices if
            choice.get("group_id") == config.get("group_ids", {}).get("design_group", -1)
            or choice["group"] == config.get("original_groups", {}).get("design_group", config["design_group"])), {})
        color = next((choice for choice in choices if
            choice.get("group_id") == config.get("group_ids", {}).get("color_group", -1)
            or choice["group"] == config.get("original_groups", {}).get("color_group", config["color_group"])), {})
        exact = next((variant.image_url for variant in variants
            if variant.option_ids and set(variant.option_ids).issubset(selected) and variant.image_url), "")
        legacy = config.get("design_previews", {}).get(str(design.get("option_id")),
            next((entry for entry in config["designs"] if entry["name"] == design.get("option")), {}))
        row["preview"] = {
            "image": exact or design.get("image_url", ""), "exact": bool(exact),
            "x": legacy.get("x", ""), "y": legacy.get("y", "0%"),
            "swatch": color.get("swatch") or next((c["hex"] for c in config["colors"]
                if c["name"] == color.get("option")), "#ffffff"),
            "label": " ".join(filter(None, [color.get("option"), design.get("option")])),
        }
    return rows


def people(item, config):
    result = []
    design_map = {row["name"]: row for row in config["designs"]}
    colors = {row["name"]: row["hex"] for row in config["colors"]}
    for person in item.participants.all():
        selected = {key: next((choice for choice in person.selections
            if choice.get("group_id") == config.get("group_ids", {}).get(key, -1)
            or choice["group"] == config.get("original_groups", {}).get(key, config[key])), {})
            for key in ("design_group", "color_group", "age_group")}
        design, color = selected["design_group"].get("option", ""), selected["color_group"].get("option", "")
        selected_color = selected["color_group"]
        result.append({"name": person.name, "age": selected["age_group"].get("option", ""), "design": design,
            "color": color, "swatch": selected_color.get("swatch") or colors.get(color, "#ffffff"), "photo": design_map.get(design), "id": person.pk,
            "selections": person.selections, "selections_deferred": person.selections_deferred})
    return selection_previews(result, item.session.offering, config)


def projection(item, config):
    title = f"{item.session_snapshot['workshop']} / {item.session_snapshot['location']}"
    booking = BookingSnapshot(str(item.public_id), title, item.purchaser_name,
        item.purchaser_email, item.requested_start, item.requested_end, item.reservation.get_state_display(),
        tuple(Participant(p.name, tuple((s["group"], s["option"]) for s in p.selections)) for p in item.participants.all()))
    return NotionDestination().project(booking, {"data_source_id": EXAMPLE_DATA_SOURCE}, config["mapping"])


def roster(profile, config, session_pk=None, *, location=None):
    from scheduling.access import staff_locations
    all_sessions = Session.objects.filter(offering__location__in=staff_locations(profile), offering__visit_program__isnull=True).select_related("offering__location").order_by("offering__location__name", "starts_at", "pk")
    if location is not None:
        all_sessions = all_sessions.filter(offering__location=location)
    upcoming = all_sessions.filter(starts_at__gt=timezone.now())
    if session_pk:
        selected = get_object_or_404(all_sessions, pk=session_pk)
    else:
        booked = WorkshopRequest.objects.filter(session__in=upcoming, reservation__state="confirmed",
            status="pending").values("session_id")
        selected = (upcoming.filter(pk__in=booked).order_by("starts_at", "pk").first()
            or upcoming.filter(offering__location__name=config["locations"][0]["name"]).first()
            or upcoming.first())
    items = list(WorkshopRequest.objects.filter(session=selected, session__offering__location__profile=profile,
        reservation__state="confirmed", status="pending")
        .select_related("reservation__order", "reservation__prepared_by").prefetch_related("participants", "reservation__stock_allocations__level__material").order_by("created_at", "pk"))
    rows, counts, missing = [], Counter(), 0
    for item in items:
        allocations = list(item.reservation.stock_allocations.all())
        item.materials_used = bool(allocations) and all(a.consumed_at for a in allocations)
        item.preparation_form = form_for(item.reservation, profile)
        for person in people(item, config):
            person["missing_selections"] = not (person["design"] and person["color"])
            missing += person["missing_selections"]
            rows.append({**person, "booking": item})
        if not item.materials_used and not item.reservation.prepared_at:
            for allocation in allocations:
                counts[(allocation.level.material.name, allocation.level.material.unit)] += allocation.quantity
    return {"sessions": all_sessions, "selected": selected, "items": items, "rows": rows,
        "totals": [{"material": key[0], "unit": key[1], "count": count} for key, count in sorted(counts.items())],
        "missing_count": missing, "prepared_count": sum(bool(item.reservation.prepared_at) and not item.materials_used for item in items)}


def calendar(item):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")

    def stamp(value):
        return value.astimezone(utc_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pontora//Local booking demo//EN", "BEGIN:VEVENT",
        f"UID:{item.public_id}@demo.pontora", f"DTSTAMP:{stamp(item.created_at)}",
        f"DTSTART:{stamp(item.requested_start)}", f"DTEND:{stamp(item.requested_end)}",
        f"SUMMARY:{escape(item.session_snapshot['workshop'])}", f"LOCATION:{escape(item.session_snapshot['location'])}",
        "DESCRIPTION:Local demonstration booking. Not a real reservation.", "END:VEVENT", "END:VCALENDAR"]
    # RFC 5545 folding counts UTF-8 octets and never splits a code point.
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode()) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"
