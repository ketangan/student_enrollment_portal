"""Single-booking email possession links exchanged for revocable sessions."""
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from core.services.url_builder import app_url, demo_url
from . import messaging, visits
from .family_access import digest, TOKEN_PATTERN
from .models import VisitAccessGrant, VisitPilotSettings, SchedulingProfile

SESSION_KEY = "bookings_visit_access"


def absolute(profile, path):
    return (demo_url if profile.school.is_demo else app_url)(path)


def notice(item, subject):
    profile = item.slot.host.profile
    if not VisitPilotSettings.objects.filter(profile=profile).exists():
        return None
    path = reverse("scheduling:visit_recover", kwargs={"school_slug": profile.school.slug})
    # A playdate supersedes pending application/tour notices for the same case.
    root = item.admission_case.tour if item.admission_case_id else None
    return messaging.capture(profile, event_key=f"visit:{item.public_id}:{item.version}:{subject}",
        group_key=f"visit:{root.public_id if root else item.public_id}", recipient=item.contact_email,
        subject=f"{profile.school}: {subject}"[:200], body=f"{subject}.\n\nOpen your private visit and application details:\n{absolute(profile, path)}\n\nUse the email address from your booking.")


@transaction.atomic
@sensitive_variables("secret")
def request_access(slug, email):
    profile = visits.public_profile(slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    if not VisitPilotSettings.objects.filter(profile=profile).exists():
        raise Http404
    # Only the recipient sees matches, not the requester. Bounded and persistently throttled.
    items = visits.bookings(profile).filter(contact_email__iexact=email).order_by("-created_at")[:10]
    for item in items:
        now = timezone.now()
        if item.access_grants.filter(created_at__gt=now-timedelta(minutes=2)).exists():
            continue
        secret = secrets.token_urlsafe(32)
        item.access_grants.filter(redeemed_at__isnull=True, revoked_at__isnull=True).update(revoked_at=now)
        grant = VisitAccessGrant.objects.create(booking=item, recipient=item.contact_email, token_hash=digest(secret),
            token_expires_at=now+timedelta(minutes=30), expires_at=now+timedelta(days=7))
        path = reverse("scheduling:visit_entry", kwargs={"school_slug": slug})
        message = messaging.capture(profile, event_key=f"visit-access:{grant.pk}", group_key=f"visit-access:{item.public_id}",
            recipient=item.contact_email, subject=f"{profile.school}: Your private visit link"[:200],
            body=f"{item.slot.program.get_kind_display()} for {item.child_name}.\nOpen your booking. This link expires in 30 minutes and works once.\n\n{absolute(profile, path)}#{secret}\n\nIf you did not request this link, ignore this message.")
        grant.delivery = message
        grant.save(update_fields=["delivery"])


@transaction.atomic
@sensitive_variables("secret", "value")
def redeem(slug, secret, session):
    profile = visits.public_profile(slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    now = timezone.now()
    grant = VisitAccessGrant.objects.select_related("booking").filter(
        booking__slot__program__offering__location__profile=profile, token_hash=digest(secret),
        token_expires_at__gt=now, expires_at__gt=now, redeemed_at__isnull=True, revoked_at__isnull=True).first() if TOKEN_PATTERN.fullmatch(secret) else None
    if not grant or grant.recipient.lower() != grant.booking.contact_email.lower():
        raise ValidationError("This link has expired or was already used. Request a new link.")
    value = secrets.token_urlsafe(32)
    grant.session_hash, grant.redeemed_at = digest(value), now
    grant.save(update_fields=["session_hash", "redeemed_at"])
    session.cycle_key()
    session[SESSION_KEY] = {**session.get(SESSION_KEY, {}), str(grant.booking.public_id): value}
    return grant.booking


def authorized(request, profile, public_id):
    from .requests import browser_key
    item = get_object_or_404(visits.bookings(profile), public_id=public_id)
    if item.browser_digest and item.browser_digest == browser_key(request):
        return item
    secret = request.session.get(SESSION_KEY, {}).get(str(public_id), "")
    if TOKEN_PATTERN.fullmatch(secret) and item.access_grants.filter(session_hash=digest(secret),
            redeemed_at__isnull=False, revoked_at__isnull=True, expires_at__gt=timezone.now(),
            recipient=item.contact_email).exists():
        return item
    raise Http404


@transaction.atomic
def revoke(profile, public_id, session=None, user=None):
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    item = get_object_or_404(visits.bookings(profile), public_id=public_id)
    grants = item.access_grants.filter(revoked_at__isnull=True)
    if session is not None:
        values = dict(session.get(SESSION_KEY, {}))
        secret = values.pop(str(public_id), "")
        grants = grants.filter(session_hash=digest(secret))
        session[SESSION_KEY] = values
    grants.update(revoked_at=timezone.now())
    # Also invalidate access acquired when booking in this browser. Never revoke
    # a different family's browser identity merely because staff opened a record.
    if session is None or item.browser_digest:
        item.browser_digest = ""
        item.save(update_fields=["browser_digest"])
    from .setup import _audit
    _audit(user, profile, item, {"family_access": "revoked"}, "change")


def can_deliver(message):
    grant = VisitAccessGrant.objects.select_related("booking").filter(delivery=message).first()
    return grant is None or (not grant.revoked_at and not grant.redeemed_at and grant.token_expires_at > timezone.now()
        and grant.recipient == grant.booking.contact_email == message.recipient)
