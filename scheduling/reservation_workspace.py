"""Read models for one reservation; no customer-specific preparation assumptions."""
from django.urls import reverse
from urllib.parse import urlencode, urlsplit
from . import preparation
from .calendar import calendar_available
from .models import PartyRequest


def participant_table(participants, catalog=()):
    columns = {("id", str(group.pk)): group.name for group in catalog if group.tracks_stock}
    rows = []
    for person in participants:
        selections = {}
        for choice in person.get("selections", []):
            key = ("id", str(choice["group_id"])) if choice.get("group_id") else ("name", choice["group"])
            columns.setdefault(key, choice["group"])
            selections.setdefault(key, []).append(choice["option"])
        if person.get("selections_deferred"):
            columns.setdefault(("status", "selection"), "Selection status")
            selections[("status", "selection")] = ["Choosing at the studio"]
        rows.append({"name": person["name"], "choices": selections, "pending_id": person.get("pending_id")})
    return {"columns": list(columns.values()), "keys": list(columns), "rows": [
        {"name": row["name"], "values": [", ".join(row["choices"].get(key,
            ["Selected"] if key == ("status", "selection") else [])) for key in columns],
         **({"pending_id": row["pending_id"]} if row["pending_id"] else {})}
        for row in rows]}


def party_participants(party):
    return [{"name": row.get("guest_name") or f"Guest {index + 1}",
             "selections": [{"group": "Selection", "option": row["name"]}]
             + ([{"group": "Age", "option": str(row["guest_age"])}] if row.get("guest_age") is not None else [])}
            for index, row in enumerate(party.materials)]


def participant_context(item):
    if item.workshop_request_id:
        participants = [{"name": p.name, "selections": p.selections, "selections_deferred": p.selections_deferred,
            "pending_id": p.pk} for p in item.workshop_request.participants.all()]
    elif item.camp_registration_id:
        participants = item.camp_registration.participants
    elif item.party_request_id:
        from .guest_lists import saved_rows
        participants = [{"name": row.get("guest_name") or f"Guest {index + 1}",
            "selections": [{"group": "Selection", "option": row.get("name") or "Not selected"}]
            + ([{"group": "Age", "option": str(row["guest_age"])}] if row.get("guest_age") is not None else [])}
            for index, row in enumerate(saved_rows(item))]
    else:
        participants = []
    return {"participants": participants, "participant_table": participant_table(participants)}


def inline_selections(request, context):
    """Associate each stock field with its existing grid column and external HTML form."""
    entries = context.get("selection_forms", [])
    if not entries:
        return
    catalog = entries[0]["form"].catalog
    table = participant_table(context["participants"], catalog)
    context["participant_table"] = table
    query = request.GET.copy()
    query.pop("edit_participant", None)
    base = request.path + ("?" + query.urlencode() if query else "")
    context["selection_return_url"] = base + "#participants-title"
    active = str(request.POST.get("participant_id", "")) if context.get("selection_error") else request.GET.get("edit_participant", "")
    by_id = {entry["person"].pk: entry for entry in entries}
    for row in table["rows"]:
        entry = by_id.get(row.get("pending_id"))
        row["cells"] = [{"value": value} for value in row["values"]]
        if not entry:
            continue
        query["edit_participant"] = entry["person"].pk
        row["edit_url"] = request.path + "?" + query.urlencode() + f'#participant-{entry["person"].pk}'
        if str(entry["person"].pk) != active:
            continue
        row["editing"] = True
        row["error"] = entry.get("error", "")
        row["form_id"] = f'participant-form-{entry["person"].pk}'
        context["inline_selection"] = entry
        entry["form_id"] = row["form_id"]
        for key, cell in zip(table["keys"], row["cells"]):
            name = f"choice_{key[1]}"
            if key[0] == "id" and name in entry["form"].fields:
                field = entry["form"][name]
                field.field.widget.attrs["form"] = row["form_id"]
                cell["field"] = field


def detail_context(request, context, item, tab="details"):
    slug = context["business"].slug
    source = item.workshop_request or item.party_request or item.camp_registration
    context.update(item=item, reservation_tab=tab,
        contact_email=getattr(source, "purchaser_email", getattr(source, "organizer_email", "")))
    offering = source.session.offering if item.workshop_request_id else source.camp.offering if item.camp_registration_id else source.offering
    context.update(participant_context(item))
    if item.workshop_request_id:
        from . import workshop_selections
        from .requests import catalog_for
        people = list(item.workshop_request.participants.all())
        context["deferred_participants"] = [p for p in people if p.selections_deferred]
        if people and offering.inventory_catalog_id and context.get("can_edit_sessions") and workshop_selections.editable(item, staff=True) and not item.stock_allocations.filter(consumed_at__isnull=False).exists():
            catalog = catalog_for(offering)
            context["selection_forms"] = [{"person": p, "form": workshop_selections.form_for(p, catalog)} for p in people]
    if item.party_request_id:
        context["party_space_label"] = dict(PartyRequest.Space.choices).get(item.snapshot.get("space"), "")
        context["party_duration"] = int((item.ends_at - item.starts_at).total_seconds() // 60)
    if item.party_request_id and offering.inventory_catalog_id:
        from .guest_lists import due_at, editable, token_for
        context.update(guest_list_due=due_at(item), guest_list_complete=bool(item.party_request.materials),
            guest_list_draft_count=len(item.snapshot.get("guest_list_draft", [])))
        if editable(item) and context.get("can_edit_sessions"):
            context["staff_guest_list_url"] = reverse("scheduling:staff_guest_list", kwargs={
                "school_slug": slug, "public_id": item.public_id})
            context["guest_list_url"] = request.build_absolute_uri(reverse("scheduling:guest_list", kwargs={
                "school_slug": slug, "token": token_for(item)}))
    context.update(reservation_location=offering.location,
        completion_label="Camp completed" if item.camp_registration_id else "Party completed" if item.party_request_id else "Workshop completed")
    context["booking_navigation"]["section"]["tabs"] = [
        {"label": "Reservation", "url": reverse("scheduling:reservation_detail", kwargs={"school_slug": slug, "public_id": item.public_id}), "active": tab == "details"},
        {"label": "Party Prep" if item.party_request_id else "Workshop Prep", "url": reverse("scheduling:reservation_preparation", kwargs={"school_slug": slug, "public_id": item.public_id}), "active": tab == "preparation"},
    ]
    # Header location changes leave this record and return to the scoped list.
    context["location_action"] = reverse("scheduling:reservations", kwargs={"school_slug": slug})
    context["location_query_fields"] = []
    prep = request.GET.get("prep", "")
    return_url = prep or request.GET.get("calendar", "")
    if return_url and len(return_url) <= 2048 and (prep or calendar_available(context["profile"])):
        try:
            target = urlsplit(return_url)
        except ValueError:
            return context
        route = "scheduling:studio_preparation" if prep else "scheduling:workspace"
        if not target.scheme and not target.netloc and target.path == reverse(route, kwargs={"school_slug": slug}):
            context["preparation_return" if prep else "calendar_return"] = return_url
            for tab in context["booking_navigation"]["section"]["tabs"]:
                tab["url"] += "?" + urlencode({"prep" if prep else "calendar": return_url})
    return context


def preparation_materials(item):
    allocations = getattr(item, "prep_allocations", None)
    if allocations is None:
        allocations = list(item.stock_allocations.select_related("level__material", "level__location"))
    frozen = {row["material_id"]: row for row in item.snapshot.get("materials", [])}
    for allocation in allocations:
        snapshot = frozen.get(allocation.level.material_id, {})
        allocation.material_name = snapshot.get("name", allocation.level.material.name)
        allocation.material_unit = snapshot.get("unit", allocation.level.material.unit)
    return allocations


def prep_context(item, profile):
    allocations = preparation_materials(item)
    party_materials = []
    customer_notes = ""
    if item.party_request_id:
        source = item.party_request
        party_materials = source.materials
        customer_notes = source.customer_notes
    return {**participant_context(item), "party_materials": party_materials,
        "customer_notes": customer_notes,
        "allocations": allocations, "materials_used": any(row.consumed_at for row in allocations),
        "materials_remaining": any(not row.consumed_at for row in allocations),
        "preparation_form": preparation.form_for(item, profile), "prep_return": "reservation"}


def selection_summary(participants):
    groups = {}
    for person in participants:
        for choice in person.get("selections", []):
            values = groups.setdefault(choice["group"], {})
            value = str(choice["option"])
            values[value] = values.get(value, 0) + 1
    return [{"label": group, "value": ", ".join(
        f"{value} x {count}" if count > 1 else value for value, count in values.items())}
        for group, values in groups.items()]
