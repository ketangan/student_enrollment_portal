"""Read models for one reservation; no customer-specific preparation assumptions."""
from django.urls import reverse
from . import preparation


def participant_table(participants):
    columns, rows = {}, []
    for person in participants:
        selections = {}
        for choice in person.get("selections", []):
            key = ("id", str(choice["group_id"])) if choice.get("group_id") else ("name", choice["group"])
            columns.setdefault(key, choice["group"])
            selections.setdefault(key, []).append(choice["option"])
        rows.append({"name": person["name"], "choices": selections})
    return {"columns": list(columns.values()), "rows": [
        {"name": row["name"], "values": [", ".join(row["choices"].get(key, [])) for key in columns]}
        for row in rows]}


def participant_context(item):
    if item.workshop_request_id:
        participants = [{"name": p.name, "selections": p.selections} for p in item.workshop_request.participants.all()]
    elif item.camp_registration_id:
        participants = item.camp_registration.participants
    else:
        participants = []
    return {"participants": participants, "participant_table": participant_table(participants)}


def detail_context(request, context, item, tab="details"):
    slug = context["business"].slug
    source = item.workshop_request or item.party_request or item.camp_registration
    context.update(item=item, reservation_tab=tab,
        contact_email=getattr(source, "purchaser_email", getattr(source, "organizer_email", "")))
    offering = source.session.offering if item.workshop_request_id else source.camp.offering if item.camp_registration_id else source.offering
    context.update(participant_context(item))
    context.update(reservation_location=offering.location,
        completion_label="Camp completed" if item.camp_registration_id else "Party completed" if item.party_request_id else "Workshop completed")
    context["booking_navigation"]["section"]["tabs"] = [
        {"label": "Reservation", "url": reverse("scheduling:reservation_detail", kwargs={"school_slug": slug, "public_id": item.public_id}), "active": tab == "details"},
        {"label": "Workshop Prep", "url": reverse("scheduling:reservation_preparation", kwargs={"school_slug": slug, "public_id": item.public_id}), "active": tab == "preparation"},
    ]
    # Header location changes leave this record and return to the scoped list.
    context["location_action"] = reverse("scheduling:reservations", kwargs={"school_slug": slug})
    context["location_query_fields"] = []
    return context


def prep_context(item, profile):
    allocations = list(item.stock_allocations.select_related("level__material", "level__location"))
    frozen = {row["material_id"]: row for row in item.snapshot.get("materials", [])}
    for allocation in allocations:
        snapshot = frozen.get(allocation.level.material_id, {})
        allocation.material_name = snapshot.get("name", allocation.level.material.name)
        allocation.material_unit = snapshot.get("unit", allocation.level.material.unit)
    party_materials = []
    customer_notes = preparation_notes = ""
    if item.party_request_id:
        source = item.party_request
        party_materials = source.materials
        customer_notes, preparation_notes = source.customer_notes, source.preparation_notes
    return {**participant_context(item), "party_materials": party_materials,
        "customer_notes": customer_notes, "preparation_notes": preparation_notes,
        "allocations": allocations, "materials_used": any(row.consumed_at for row in allocations),
        "materials_remaining": any(not row.consumed_at for row in allocations),
        "preparation_form": preparation.form_for(item, profile), "prep_return": "reservation"}
