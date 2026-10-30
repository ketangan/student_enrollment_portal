"""School-scoped staff composition using the existing reservation outbox."""
import uuid
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.html import escape

from core.models import School, SchoolEmailTemplate
from core.payments.checkout import amount_due
from core.payments.models import PaymentOrder
from core.payments.service import totals
from core.services.email_content import clean_html, render_tokens, validate_body
from core.services.url_builder import app_reverse, demo_reverse
from . import guest_lists, messaging, party_notifications, workshop_followup, workshop_notifications
from .access import get_staff_reservation, require_location_access
from .capabilities import has_capability
from .checkout import payment_request_url, validate_booking_checkout
from .lessons import digest
from .models import SchedulingProfile

SALT = "scheduling.manual-email.v1"
LINK_SALT = "scheduling.manual-email-link.v1"
MANUAL_KINDS = ("manual", "manual_payment", "manual_guests")


def demo_recipients(profile):
    from .experience_demo.config import available, hosted_demo
    mapping = getattr(settings, "BOOKINGS_DEMO_EMAIL_RECIPIENTS", {})
    if not isinstance(mapping, dict) or not hosted_demo(profile.school.slug) or not available(profile):
        return ()
    values = mapping.get(profile.school.slug, [])
    if not isinstance(values, list) or not values or len(values) > 20:
        return ()
    try:
        for value in values:
            if not isinstance(value, str):
                return ()
            validate_email(value)
    except ValidationError:
        return ()
    return tuple(value.strip().lower() for value in values)


def external_delivery_allowed(item, recipient):
    if not enabled(item):
        return False
    if item.profile.school.is_demo:
        return item.order.environment == "test" and recipient.strip().lower() in demo_recipients(item.profile)
    return item.order.environment == "live" and party_notifications.live_enabled(item.profile)


def templates_for(item):
    kind = "party" if item.party_request_id else "workshop"
    return SchoolEmailTemplate.objects.filter(school=item.profile.school, purpose="bookings",
        is_active=True, booking_kind__in=["both", kind]).order_by("name", "pk")


def variables(item):
    source = item.party_request if item.party_request_id else item.workshop_request
    name = source.organizer_name if item.party_request_id else source.purchaser_name
    email = source.organizer_email if item.party_request_id else source.purchaser_email
    when = timezone.localtime(item.starts_at, ZoneInfo(item.snapshot.get("timezone", "UTC")))
    location = source.offering.location if item.party_request_id else source.session.offering.location
    return {"full_name": name, "first_name": name.split()[0] if name.split() else "",
        "email": email, "program": item.snapshot.get("workshop", ""), "status": item.get_state_display(),
        "school_name": item.profile.school.display_name or item.profile.school.slug,
        "booking_date": when.strftime("%a, %b %d, %Y"), "booking_time": when.strftime("%I:%M %p %Z"),
        "location": location.name, "participant_count": item.quantity}


def customer_link(item, kind):
    """Only offer actions that the existing customer pages currently accept."""
    school = item.profile.school
    reverse = demo_reverse if school.is_demo else app_reverse
    kwargs = {"school_slug": school.slug}
    if kind == "payment":
        try:
            validate_booking_checkout(item)
        except ValidationError:
            return None
        due = amount_due(item.order)
        if due < 50:
            return None
        url = payment_request_url(item)
        return {"kind": kind, "url": url, "label": f"Pay ${due / 100:.2f}",
            "hint": f"Adds a payment link for ${due / 100:.2f} to this email.",
            "signature": digest({"due": due, "balance": totals(item.order)["outstanding_cents"],
                "account": item.order.account_id, "state": item.state, "order": item.order_id})}
    if kind == "guests" and item.profile.enabled and guest_lists.editable(item):
        offering = item.party_request.offering if item.party_request_id else item.workshop_request.session.offering
        if not offering.inventory_catalog_id or item.stock_allocations.filter(consumed_at__isnull=False).exists():
            return None
        party = bool(item.party_request_id)
        url = reverse("scheduling:guest_list" if party else "scheduling:workshop_selections", kwargs={
            **kwargs, "token": guest_lists.token_for(item) if party else workshop_followup.token_for(item)})
        label = "Add guest details" if party else "Update participant selections"
        return {"kind": kind, "url": url, "label": label,
            "hint": f'Adds an "{label}" button to this email.',
            "signature": digest({"reservation": item.pk, "state": item.state,
                "start": item.starts_at.isoformat(), "catalog": offering.inventory_catalog_id})}
    return None


def draft_options(item):
    values = variables(item)
    links = {kind: customer_link(item, kind) for kind in ("payment", "guests")}
    options = {"": {"name": "Blank message", "subject": "", "body": "", "link": None}}
    for template in templates_for(item):
        link = links.get(template.booking_link)
        options[str(template.pk)] = {"name": template.name, "subject": render_tokens(template.subject, values),
            "body": clean_html(render_tokens(template.body, values, html=True)), "link": link,
            "unavailable": template.booking_link != "none" and not link}
    return options


class TemplateSelect(forms.Select):
    def __init__(self, unavailable):
        super().__init__()
        self.unavailable = unavailable

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if str(value) in self.unavailable:
            option["attrs"]["disabled"] = True
        return option


class ComposeForm(forms.Form):
    template = forms.ChoiceField(required=False, label="Template")
    recipient = forms.EmailField(label="To", max_length=254)
    subject = forms.CharField(max_length=200)
    body = forms.CharField(label="Message", max_length=20000, widget=forms.Textarea(attrs={"rows": 10}))
    email_token = forms.CharField(widget=forms.HiddenInput)
    link_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, item, options=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.options = draft_options(item) if options is None else options
        self.fields["template"].widget = TemplateSelect({key for key, option in self.options.items() if option.get("unavailable")})
        self.fields["template"].choices = [(key, option["name"]) for key, option in self.options.items()]

    def clean_template(self):
        key = self.cleaned_data["template"]
        if self.options[key].get("unavailable"):
            raise ValidationError("This template's payment or guest-details button is unavailable for this reservation. Choose another template.")
        return key

    def clean_subject(self):
        subject = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise ValidationError("Enter a single-line subject.")
        return subject

    def clean_body(self):
        return validate_body(self.cleaned_data["body"])

    def clean(self):
        data = super().clean()
        for field in ("subject", "body"):
            if "{{" in data.get(field, "") or "}}" in data.get(field, ""):
                self.add_error(field, "Replace the remaining template fields before sending.")
        return data


def context_for(user, item, form=None):
    if not (item.party_request_id or item.workshop_request_id):
        return {}
    values = variables(item)
    initial = {"recipient": values["email"], "email_token": signing.dumps({
        "user": user.pk, "reservation": item.pk, "key": str(uuid.uuid4())}, salt=SALT)}
    options = draft_options(item)
    for option in options.values():
        link = option["link"]
        option["link_hint"] = link["hint"] if link else ""
        option["link_token"] = signing.dumps({"user": user.pk, "reservation": item.pk, **link}, salt=LINK_SALT) if link else ""
        # URLs are signed server-side; the UI displays only the action description.
        option.pop("link")
    return {"email_form": form if form is not None else ComposeForm(item=item, options=options, initial=initial),
        "email_options": options, "email_customer_name": values["first_name"] or "customer"}


def enabled(item):
    profile = item.profile
    return (profile.enabled and profile.party_notifications_enabled and has_capability(profile, "parties")) if item.party_request_id else workshop_notifications.enabled(profile)


def deliverable(message, item):
    if item.profile_id != message.profile_id or not message.created_by_id or not enabled(item):
        return False
    try:
        profile, _ = require_location_access(message.created_by, item.profile.school.slug, "sessions.edit", capability="reservations")
        get_staff_reservation(profile, item.public_id, exclude_visits=True)
    except Http404:
        return False
    if message.notice_kind in {"manual_payment", "manual_guests"}:
        link = customer_link(item, message.notice_kind.removeprefix("manual_"))
        return bool(link and message.notice_signature == link["signature"])
    return True


@transaction.atomic
def send(user, slug, public_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    School.objects.select_for_update().get(pk=profile.school_id)
    item.order = PaymentOrder.objects.select_for_update(of=("self",)).select_related("account").get(pk=item.order_id)
    form = ComposeForm(data, item=item)
    if not form.is_valid():
        return form, None
    try:
        token = signing.loads(form.cleaned_data["email_token"], salt=SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if token["user"] != user.pk or token["reservation"] != item.pk:
            raise ValueError
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        form.add_error(None, "This draft has expired. Refresh the reservation before sending.")
        return form, None
    link = form.options[form.cleaned_data["template"]]["link"]
    body = form.cleaned_data["body"]
    if not link and form.cleaned_data["link_token"]:
        form.add_error(None, "The included link is no longer available. Review this email before sending again.")
        return form, None
    if link:
        try:
            supplied = signing.loads(form.cleaned_data["link_token"], salt=LINK_SALT, max_age=7200)
            if (supplied["user"] != user.pk or supplied["reservation"] != item.pk or
                    supplied["kind"] != link["kind"] or supplied["signature"] != link["signature"]):
                raise ValueError
        except (signing.BadSignature, KeyError, ValueError, TypeError):
            form.add_error(None, "The included link or payment amount changed. Review the link below before sending again.")
            return form, None
        body += f'<p><a class="booking-email-action" href="{escape(supplied["url"])}">{escape(link["label"])}</a></p>'
    live = external_delivery_allowed(item, form.cleaned_data["recipient"])
    if not live and not (settings.DEBUG and item.order.environment == "test"):
        form.add_error(None, "Email delivery is disabled for this workspace. Check Guest communication settings.")
        return form, None
    try:
        message = messaging.capture(profile, event_key=f"manual:{item.public_id}:{key}", group_key=f"manual:{key}",
            recipient=form.cleaned_data["recipient"], subject=form.cleaned_data["subject"], body=body)
    except ValidationError as exc:
        form.add_error(None, exc)
        return form, None
    # A repeated POST must never requeue a message already accepted or being processed.
    if not message.reservation_id:
        message.reservation = item
        message.created_by = user
        message.notice_kind = "manual_" + link["kind"] if link else "manual"
        message.notice_signature = link["signature"] if link else ""
        message.is_html = True
        message.state = "queued" if live else "draft"
        message.save()
    return form, message
