"""Customer amendments before payment, using the existing booking and inventory locks."""
from datetime import timedelta

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import QueryDict
from django.utils import timezone

from core.models import School
from core.payments.models import PaymentOrder, PaymentAttempt
from . import reservations, requests, inventory
from .models import Reservation, SchedulingProfile, RequestParticipant

SALT = "booking-unpaid-edit"


def editable(item):
    return (item.state == "held" and bool(item.order.account_id) and not item.order.entries.exists()
            and not item.stock_allocations.filter(consumed_at__isnull=False).exists()
            and not item.order.attempts.filter(state="paid").exists())


def revision(item):
    return item.snapshot.get("customer_edit_revision", 0)


def edit_token(item):
    return signing.dumps({"id": str(item.public_id), "revision": revision(item)}, salt=SALT)


def check_token(item, token):
    try:
        value = signing.loads(token, salt=SALT, max_age=7200)
        if value != {"id": str(item.public_id), "revision": revision(item)}:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError("This booking changed in another tab. Reopen Edit details before saving.") from None


def lock(item):
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    School.objects.select_for_update().get(pk=item.profile.school_id)
    PaymentOrder.objects.select_for_update().get(pk=item.order_id)
    return Reservation.objects.select_for_update(of=("self",)).select_related("order__account", "profile__school").get(pk=item.pk)


def require_editable(item):
    if not editable(item):
        raise ValidationError("Payment has been received or this booking is no longer editable. Please contact the studio.")


def close_checkout(item, token):
    """Commit the close request before provider I/O; uncertain payments block saving."""
    from .checkout import recover_attempt
    from .square_checkout import recover_square_attempt
    with transaction.atomic():
        item = lock(item)
        require_editable(item)
        check_token(item, token)
        attempts = list(item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE))
        for attempt in attempts:
            attempt.close_requested = True
            attempt.save(update_fields=["close_requested"])
    for attempt in attempts:
        if attempt.account.provider == "square" and not attempt.submitted_at:
            recover_square_attempt(attempt)
        else:
            recover_attempt(attempt, force=True)
    with transaction.atomic():
        item = lock(item)
        require_editable(item)
        if item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
            raise ValidationError("Your payment is still being verified. Your changes have not been saved. Please try again once verification finishes.")


def party_initial(item):
    source = item.party_request
    values = model_to_dict(source)
    values["participant_count_mode"] = "estimate"
    values["participant_estimate"] = source.participant_estimate or str(source.group_size)
    for kind, record in source.offering_snapshot.get("acknowledgments", {}).get("items", {}).items():
        values[f"{kind}_acknowledged"] = record.get("accepted", False)
    return values


def workshop_data(item, session):
    source = item.workshop_request
    data = QueryDict(mutable=True)
    data.update({"purchaser_name": source.purchaser_name, "purchaser_email": source.purchaser_email,
                 "purchaser_phone": source.purchaser_phone, "policy_ack": "on",
                 "participants-INITIAL_FORMS": "0"})
    people = list(source.participants.all())
    if people and all(person.selections_deferred for person in people):
        data["choose_when"] = "later"
    data["participants-TOTAL_FORMS"] = str(len(people))
    for index, person in enumerate(people):
        prefix = f"participants-{index}-"
        data[prefix + "name"] = person.name
        if person.selections_deferred:
            data[prefix + "selections_deferred"] = "on"
        for selection in person.selections:
            data[prefix + f'choice_{selection["group_id"]}'] = str(selection["option_id"])
    return data


def finish(item, total, deposit, description, token, digest):
    # Old attempts are immutable. Only a verified unpaid commercial quote can change.
    if item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
        raise ValidationError("A checkout started in another tab. Save again after it has been closed.")
    before = {"total_cents": item.order.total_cents, "deposit_cents": item.order.deposit_cents,
              "snapshot": Reservation.objects.get(pk=item.pk).snapshot}
    item.stock_allocations.all().delete()
    item.hold_until = min(timezone.now() + timedelta(minutes=30), item.starts_at)
    item.snapshot["customer_edit_revision"] = revision(item) + 1
    item.snapshot["customer_edit_receipt"] = {"token": token, "digest": digest}
    if total == 0:
        item.state = "confirmed"
    item.save()
    inventory.allocate(item)
    reservations.ensure_capacity(item)
    item.order.total_cents, item.order.deposit_cents = total, deposit
    item.order.description = description
    item.order.full_clean()
    item.order.save(update_fields=["total_cents", "deposit_cents", "description"])
    reservations._audit(None, item, "customer_updated_unpaid_booking", before=before,
                        total_cents=total, deposit_cents=deposit)
    return item


def already_saved(item, token, data):
    return item.snapshot.get("customer_edit_receipt") == {"token": token, "digest": requests.submission_digest(data)}


def save_party(item, offering, data, token):
    from .party_forms import PartyInquiryForm
    from .parties import inquiry_digest, TOKEN_SALT
    if already_saved(item, token, data):
        return item
    if offering.location.profile_id != item.profile_id:
        raise ValidationError("Choose a studio for this business.")
    close_checkout(item, token)
    with transaction.atomic():
        item = lock(item)
        if already_saved(item, token, data):
            return item
        require_editable(item)
        check_token(item, token)
        try:
            reviewed = signing.loads(data.get("request_token", ""), salt=TOKEN_SALT, max_age=7200)
            if reviewed.get("offering") != offering.pk or reviewed.get("configuration") != inquiry_digest(offering):
                raise signing.BadSignature
        except signing.BadSignature:
            raise ValidationError("The package details changed. Reopen Edit details to review the updated options.") from None
        form = PartyInquiryForm(data, offering=offering, exclude_reservation=item.pk)
        if not form.is_valid():
            raise ValidationError("The party options changed. Review the form again.")
        if reservations.booking_account(item.profile, True, offering.location).pk != item.order.account_id:
            raise ValidationError("This studio uses a different payment account. Contact the studio to move this booking.")
        source = item.party_request
        source.offering = offering
        for name, value in form.inquiry_values().items():
            setattr(source, name, value)
        source.offering_snapshot = {"workshop": offering.name, "location": offering.location.name,
            "timezone": offering.location.timezone_name, "acknowledgments": form.acknowledgment_record()}
        source.revision += 1
        source.save()
        replacement = reservations.party_reservation_item(item.profile, source)
        for name in ("room", "starts_at", "ends_at", "quantity", "exclusive"):
            setattr(item, name, getattr(replacement, name))
        replacement.snapshot["customer_edit_revision"] = revision(item)
        item.snapshot = replacement.snapshot
        return finish(item, int(source.proposed_total * 100), int(source.proposed_deposit * 100),
                      offering.name, token, requests.submission_digest(data))


def save_workshop(item, session, data, token, config, *, review_token, browser):
    from .experience_demo import service
    if already_saved(item, token, data):
        return item
    if session.offering.location.profile_id != item.profile_id:
        raise ValidationError("Choose a workshop for this business.")
    close_checkout(item, token)
    with transaction.atomic():
        item = lock(item)
        if already_saved(item, token, data):
            return item
        require_editable(item)
        check_token(item, token)
        session = requests.session_for_request(item.profile.school.slug, session.pk)
        catalog, purchaser, people, policy, valid = service.validate(session, data, check_stock=False)
        try:
            expected = signing.loads(service.review_token(session, data, catalog, browser, config), salt=service.REVIEW_SALT)
            if signing.loads(review_token, salt=service.REVIEW_SALT, max_age=900) != expected:
                raise signing.BadSignature
        except signing.BadSignature:
            raise ValidationError("The workshop price or policies changed. Review your booking again.") from None
        if (not valid or not requests.accepts_reservations(session, catalog)
                or session.starts_at <= service.booking_cutoff(online=True)):
            raise ValidationError("The workshop or selections changed. Review your details again.")
        if reservations.booking_account(item.profile, True, session.offering.location).pk != item.order.account_id:
            raise ValidationError("This studio uses a different payment account. Contact the studio to move this booking.")
        source = item.workshop_request
        source.session = session
        for name, value in purchaser.cleaned_data.items():
            setattr(source, name, value)
        breakdown = reservations.workshop_price_breakdown(session.offering, config["price_cents"], len(people))
        source.session_snapshot = {"workshop": session.offering.name, "location": session.offering.location.name,
            "room": session.room.name if session.room else "", "timezone": session.offering.location.timezone_name,
            "starts_at": session.starts_at.isoformat(), "ends_at": session.ends_at.isoformat(),
            "experience_demo": {"policy": config["policy_revision"], "published_policy": service.notice(item.profile.school, "workshop"),
                "reviewed_at": timezone.now().isoformat(), "price_cents": config["price_cents"]}, "price_breakdown": breakdown}
        source.save()
        source.participants.all().delete()
        RequestParticipant.objects.bulk_create([RequestParticipant(request=source, position=index,
            name=form.cleaned_data["name"], **form.participant_snapshot()) for index, form in enumerate(people)])
        item.session, item.room = session, session.room
        item.starts_at, item.ends_at, item.quantity = session.starts_at, session.ends_at, len(people)
        item.snapshot = {"name": source.purchaser_name, "workshop": session.offering.name,
            "organizer_phone": source.purchaser_phone, "timezone": session.offering.location.timezone_name,
            "customer_edit_revision": revision(item)}
        total, deposit = reservations.workshop_quote(session.offering, config["price_cents"], len(people))
        return finish(item, total, deposit, session.offering.name, token, requests.submission_digest(data))
