"""Fictional customer bookings made through the normal reservation services."""
import uuid
from scheduling import reservations, preparation, completion
from scheduling.models import WorkshopRequest, RequestParticipant, Session
from scheduling.requests import catalog_for


def seed_reservations(profile, user, config):
    for location in profile.locations.all():
        sessions = list(Session.objects.filter(offering__location=location, offering__name="Plushie-making workshop").order_by("starts_at")[:3])
        for index, (name, names) in enumerate([("Jordan Miller", ["Mila", "Theo", "Ava"]),
                ("Alex Chen", ["Emma", "Noah"]), ("Sam Rivera", ["Olivia"])]):
            session = sessions[index]
            source = WorkshopRequest.objects.create(session=session, purchaser_name=name,
                purchaser_email=f"demo-{location.pk}-{index}@example.invalid", submission_key=uuid.uuid4(),
                submission_digest="seed", browser_digest="seed", session_snapshot={"workshop": session.offering.name,
                    "location": location.name, "timezone": location.timezone_name,
                    "starts_at": session.starts_at.isoformat(), "ends_at": session.ends_at.isoformat()})
            groups = catalog_for(session.offering)
            for position, child in enumerate(names):
                selections = []
                for group in groups:
                    choices = group.active_options
                    option = choices[(position + index) % len(choices)]
                    selections.append({"group_id": group.pk, "group": group.name, "option_id": option.pk, "option": option.label})
                RequestParticipant.objects.create(request=source, name=child, position=position, selections=selections)
            booking = reservations.create_reservation(user, profile.school.slug, "workshop", source.public_id,
                total_cents=len(names) * config["price_cents"], deposit_cents=0)
            reservations.settle_offline(user, profile.school.slug, booking.public_id, key=uuid.uuid4(),
                kind="payment", amount_cents=booking.order.total_cents, reference=f"Demo booking {booking.public_id}")
            if index == 1:
                booking.refresh_from_db()
                form = preparation.form_for(booking, profile)
                preparation.update(user, profile.school.slug, booking.public_id,
                    {"action": "prepare", "edit_revision": form["edit_revision"].value()})
    # A completed and a canceled record make stock history visible in the demo.
    first = profile.reservations.filter(snapshot__name="Sam Rivera").first()
    completion.complete(user, profile.school.slug, first.public_id)
    second = profile.reservations.filter(snapshot__name="Sam Rivera").exclude(pk=first.pk).first()
    reservations.cancel_reservation(user, profile.school.slug, second.public_id)
