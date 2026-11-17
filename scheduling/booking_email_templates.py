"""Opt-in booking email presentation; existing outbox eligibility owns delivery."""
import re
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

from core.models import SchoolEmailTemplate
from core.services.email_content import clean_html, render_tokens, transport_html
from core.services.url_builder import app_reverse, demo_reverse


DEFAULTS = {
    "party_confirmation": ("Party confirmation", "Your party is confirmed", "party", "guests",
        "Your party is confirmed! We look forward to celebrating with you."),
    "party_reminder": ("Guest-selection reminder", "Your party guest selections are due", "party", "guests",
        "Please complete your guest selections so our team can prepare for your party."),
    "workshop_confirmation": ("Workshop confirmation", "Your workshop is confirmed", "workshop", "guests",
        "We look forward to welcoming you to {{business_name}}!"),
    "payment_request": ("Party payment request", "Payment requested", "party", "payment",
        "Your party space is reserved. Please complete your payment using the link below."),
    "rescheduled": ("Workshop schedule update", "Your workshop time has changed", "workshop", "none",
        "Please review the updated details below. Contact the studio with any questions."),
    "canceled": ("Workshop cancellation", "Your workshop has been canceled", "workshop", "none",
        "Please contact the studio if you have any questions about your canceled workshop."),
}

LINK_CHOICES = (('payment', 'Make payment'), ('guests', 'Guest details / design selections'),
    ('guide', 'Design guide (PDF)'))
DETAIL_TOKENS = (('package_name', 'Package / workshop'), ('starts_at', 'Starts'),
    ('ends_at', 'Ends'), ('room', 'Room'))


def selected_links(template):
    if template.booking_links is not None:
        return [key for key, _ in LINK_CHOICES if key in template.booking_links]
    links = [template.booking_link] if template.booking_link != 'none' and (
        not template.booking_event or template.booking_action_enabled) else []
    if template.booking_event in {'party_confirmation', 'party_reminder', 'workshop_confirmation'} and template.booking_guide_enabled:
        links.append('guide')
    return list(dict.fromkeys(links))


def editable_body(template):
    if not template.booking_event or template.booking_details_in_body:
        return template.body
    label = 'Package' if template.booking_kind == 'party' else 'Workshop'
    return template.body + ('<p><strong>' + label + ':</strong> {{package_name}}</p>'
        '<p><strong>Location:</strong> {{location}}</p><p><strong>Room:</strong> {{room}}</p>'
        '<p><strong>Starts:</strong> {{starts_at}}</p>'
        '<p><strong>Ends:</strong> {{ends_at}}</p><p><strong>Participants:</strong> {{participant_count}}</p>')


def default_template(school, event):
    name, title, kind, link, text = DEFAULTS[event]
    return SchoolEmailTemplate(school=school, purpose="bookings", booking_event=event,
        name=name, subject="{{business_name}}: " + title, heading=title,
        booking_kind=kind, booking_link=link,
        body="<p>Hi {{first_name}},</p><p>" + text + "</p>")


@transaction.atomic
def seed_defaults(profile):
    """Explicit activation only. Never replace school-authored templates."""
    from .models import SchedulingProfile
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    for event in DEFAULTS:
        template = default_template(profile.school, event)
        SchoolEmailTemplate.objects.get_or_create(school=profile.school, purpose="bookings",
            booking_event=event, defaults={field: getattr(template, field)
                for field in ("name", "subject", "heading", "body", "booking_kind", "booking_link")})


def brand(profile):
    return {"brand": profile.email_brand_name or profile.school.display_name or profile.school.slug,
        "color": profile.email_brand_color, "signoff": profile.email_signoff}


def facts(item, event, *, before=None, refund_requested=False, customer_message=""):
    from . import guest_lists, workshop_followup
    from .checkout import payment_request_url
    from core.payments.checkout import amount_due
    from core.payments.service import totals
    party = bool(item.party_request_id)
    source = item.party_request if party else item.workshop_request
    offering = source.offering if party else source.session.offering
    zone = ZoneInfo(item.snapshot.get("timezone") or offering.location.timezone_name)
    when = lambda value: timezone.localtime(value, zone).strftime("%b %d, %Y, %I:%M %p %Z")
    location = source.offering_snapshot.get("location") if party else None
    details = [("Package" if party else "Workshop",
        item.snapshot.get("party_package", {}).get("name") or offering.name),
        ("Location", location or offering.location.name)]
    if item.room_id:
        details.append(("Room", item.room.name))
    if before:
        details.append(("Previous time", when(datetime.fromisoformat(before["start"]))))
    details.extend([("Starts", when(item.starts_at)), ("Ends", when(item.ends_at)),
        ("Participants", str(item.quantity))])
    paragraphs, actions = [], []
    school = item.profile.school
    reverse = demo_reverse if school.is_demo else app_reverse
    if event in {"party_confirmation", "party_reminder", "workshop_confirmation"}:
        if offering.inventory_catalog_id:
            if party:
                from .party_notifications import needs_guests
                due = guest_lists.due_at(item)
                if needs_guests(item):
                    paragraphs.append("Please submit your guest selections by " + when(due) + "."
                        if due and due > timezone.now() else "Please submit your guest selections now so the team can prepare.")
            else:
                paragraphs.append("You can choose before your visit or at the studio, subject to availability.")
            actions.append({"kind": "guests", "label": "Add guest details" if party else "Choose your designs",
                "url": reverse("scheduling:guest_list" if party else "scheduling:workshop_selections",
                    kwargs={"school_slug": school.slug, "token": guest_lists.token_for(item) if party else workshop_followup.token_for(item)})})
        if item.profile.design_guides.exists():
            actions.append({"kind": "guide", "label": "Design guide (PDF)", "url": reverse("scheduling:design_guide", kwargs={"school_slug": school.slug})})
    if event == "payment_request" or event == "workshop_confirmation" and item.snapshot.get("staff_workshop"):
        money = totals(item.order)
        currency = item.order.currency.upper()
        details.extend([("Reservation total", f"{currency} {money['total']:.2f}"),
                        ("Balance due", f"{currency} {money['outstanding']:.2f}")])
        if event == "payment_request":
            due = amount_due(item.order)
            details.append(("Deposit due" if due < money["outstanding_cents"] else "Amount due", f"{currency} {due / 100:.2f}"))
            if due < money["outstanding_cents"]:
                paragraphs.append(f"You can also pay the full remaining amount of {currency} {money['outstanding']:.2f}.")
            actions.append({"kind": "payment", "label": "Make payment", "url": payment_request_url(item)})
        else:
            paragraphs.append("Your places are reserved. Please arrange any outstanding payment with the studio.")
    if event == "rescheduled":
        paragraphs.append("Your agreed price and payments have not changed.")
    if event == "canceled" and refund_requested:
        paragraphs.append("A refund has been requested to the original payment method. Processing time depends on the payment provider.")
    if customer_message:
        paragraphs.append(customer_message)
    return {"details": details, "paragraphs": paragraphs, "actions": actions}


def payload(item, event, subject, body, **context):
    if not item.profile.branded_booking_email:
        return {"subject": subject, "body": body}
    from .reservation_email import variables
    template = SchoolEmailTemplate.objects.filter(school=item.profile.school, purpose="bookings",
        booking_event=event, is_active=True).first() or default_template(item.profile.school, event)
    from .reservation_email import customer_link
    details = facts(item, event, **context)
    event_actions = {action['kind']: action for action in details['actions']}
    details['actions'] = [link for kind in selected_links(template)
        if (link := event_actions.get(kind) or customer_link(item, kind))]
    for action in details['actions']:
        if action['kind'] == 'payment':
            action['label'] = 'Make payment'
    return render_payload(item.profile, template, variables(item), details)


def render_payload(profile, template, values, details):
    values = {**values, 'customer_name': values.get('customer_name', values.get('full_name', '')),
        'business_name': brand(profile)['brand']}
    details = {**details, 'actions': [a for a in details.get('actions', [])
        if a.get('kind') in selected_links(template)]}
    if template.booking_event:
        details['details'] = [(label, value) for label, value in details.get('details', [])
            if label not in {'Package', 'Workshop', 'Location', 'Room', 'Starts', 'Ends', 'Participants'}]
    subject = render_tokens(template.subject, values).replace("\r", " ").replace("\n", " ")
    return {"subject": subject[:200], "body": clean_html(render_tokens(editable_body(template), values, html=True)),
        "email_layout": {**brand(profile), "heading": render_tokens(template.heading, values), **details}}


def presentation(body, layout, *, preview=False):
    """Only sanitized wording is HTML; all layout data is escaped by Django."""
    layout = {**layout}
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", layout.get("color", "")):
        layout["color"] = "#2563eb"
    channels = [int(layout['color'][i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    luminance = sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))
    layout['button_text'] = '#ffffff' if 1.05 / (luminance + .05) >= 4.5 else '#111111'
    layout["actions"] = [a for a in layout.get("actions", [])
        if a.get("url", "").startswith(("https://", "http://"))]
    safe_body = transport_html(body)
    if preview:
        safe_body = clean_html(body, links=False)
    html = render_to_string("scheduling/email/branded.html", {"email": layout, "body": safe_body, "preview": preview})
    # Explicit separators preserve paragraph/list structure in plain-text clients.
    plain = unescape(strip_tags(re.sub(r"</(?:p|div|li)>|<br\s*/?>", "\n", clean_html(body))))
    text = "\n\n".join(filter(None, [layout.get("brand"), layout.get("heading"), plain,
        "\n".join(f"{label}: {value}" for label, value in layout.get("details", [])),
        "\n\n".join(layout.get("paragraphs", [])),
        "\n".join(f"{a['label']}: {a['url']}" for a in layout["actions"]), layout.get("signoff")]))
    return text, html


def make_email(message, legacy_factory, **kwargs):
    if not message.email_layout:
        email = legacy_factory(subject=message.subject, body=message.body, **kwargs)
        if message.is_html:
            email.body = transport_html(message.body)
            email.content_subtype = "html"
        return email
    text, html = presentation(message.body, message.email_layout)
    email = EmailMultiAlternatives(subject=message.subject, body=text, **kwargs)
    email.attach_alternative(html, "text/html")
    return email


def preview(profile, template):
    values = {"first_name": "Maya", "full_name": "Maya Chen", "school_name": profile.school.display_name or profile.school.slug,
        "email": "maya@example.invalid", "program": "Workshop", "status": "Confirmed",
        "booking_date": "Nov 24, 2026", "booking_time": "10:00 AM PST", "location": "Studio", "participant_count": 3}
    values.update(package_name='Party package' if template.booking_kind == 'party' else 'Workshop',
        starts_at='Nov 24, 2026, 10:00 AM PST', ends_at='Nov 24, 2026, 12:00 PM PST', room='Studio')
    details = {"details": [("Workshop", "Workshop"), ("Starts", "Nov 24, 2026, 10:00 AM PST"),
        ("Participants", "3")], "paragraphs": [], "actions": []}
    if template.booking_kind == "party":
        details["details"][0] = ("Package", "Party package")
    if template.booking_event == "payment_request":
        details["details"].extend([("Reservation total", "USD 500.00"), ("Balance due", "USD 350.00")])
    if template.booking_event == "rescheduled":
        details["details"].insert(1, ("Previous time", "Nov 24, 2026, 9:00 AM PST"))
        details["paragraphs"].append("Your agreed price and payments have not changed.")
    if not template.booking_event:
        details["details"] = []
    details['actions'] = [{'kind': key, 'label': ('Add guest details' if template.booking_kind == 'party'
        else 'Update participant selections') if key == 'guests' else label, 'url': 'https://example.invalid/preview'}
        for key, label in LINK_CHOICES if key in selected_links(template)]
    data = render_payload(profile, template, values, details)
    return {"subject": data["subject"], "html": presentation(data["body"], data["email_layout"], preview=True)[1]}


def brand_preview(profile):
    body = '<p>Hi Maya,</p><p>Your email message appears here.</p>'
    if not profile.branded_booking_email:
        return {'subject': '', 'html': '<!doctype html><html><body>' + body + '</body></html>'}
    return {'subject': '', 'html': presentation(body, brand(profile), preview=True)[1]}
