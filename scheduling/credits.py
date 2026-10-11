"""Staff-only, tenant-serialized make-up entitlements. Never creates money entries."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .credit_forms import CreditPolicyForm, RedeemCreditForm, RevokeCreditForm
from .credit_models import LessonCancellation, LessonCredit, LessonCreditEvent, LessonCreditPolicy
from .editing import EditRevisionField
from .lesson_models import LessonOccurrence, LessonSeries
from .lessons import audit, digest, lesson_access, plan_dates, preview_revision

CREDIT_SALT = "scheduling.lesson-credit.v1"
CANCEL_SALT = "scheduling.lesson-cancel.v1"


def policy_for(profile):
    return LessonCreditPolicy.objects.filter(profile=profile).first() or LessonCreditPolicy(profile=profile)


def policy_snapshot(policy):
    return {name: getattr(policy, name) for name in ["enabled", "family_cancellations", "studio_cancellations",
                                                  "notice_hours", "expiry_days", "version"]}


def policy_form(profile, data=None):
    policy = policy_for(profile)
    form = CreditPolicyForm(data, instance=policy)
    form.fields["edit_revision"] = EditRevisionField({"profile": profile.pk, "policy": policy.pk,
                                                     "state": digest(policy_snapshot(policy))})
    return form


@transaction.atomic
def save_policy(user, slug, data):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    form = policy_form(profile, data)
    if form.is_valid():
        policy = form.instance
        policy.version += 1
        policy.save()
        audit(user, profile, policy, "change", policy_snapshot(policy))
    return form


def credit_record(user, credit, kind, occurrence, reason, key=None):
    event = LessonCreditEvent.objects.create(credit=credit, kind=kind, occurrence=occurrence, actor=user,
        reason=reason, **({"key": key} if key else {}))
    audit(user, credit.profile, credit, "change", {"action": kind, "event": event.pk,
        "occurrence": occurrence.pk if occurrence else None, "reason": reason})
    return event


def cancellation_outcome(item, policy, origin):
    credit = LessonCredit.objects.filter(used_occurrence=item).first()
    if credit:
        saved = credit.snapshot["policy"]
        timely = item.starts_at - timezone.now() >= timedelta(hours=saved["notice_hours"])
        if origin == "studio" or timely:
            return "restored", "Original credit restored; its expiry does not change.", credit
        return "forfeited", "Make-up credit forfeited: insufficient family notice.", credit
    snapshot = item.series.snapshot
    if "booking_offer" in snapshot and not snapshot.get("makeup_eligible", False):
        return "none", "No credit: this booking does not include make-up credits.", None
    if not policy.enabled:
        return "none", "No credit: make-up credits are disabled.", None
    if not getattr(policy, f"{origin}_cancellations"):
        return "none", f"No credit: {origin} cancellations do not qualify.", None
    if origin == "family" and item.starts_at - timezone.now() < timedelta(hours=policy.notice_hours):
        return "none", "No credit: insufficient family notice.", None
    return "issued", "One make-up credit for the same student, service and duration.", None


def cancellation_plan(item, scope, origin):
    if scope not in {"one", "future"} or origin not in {"family", "studio"}:
        raise ValidationError("Choose a cancellation scope and who requested it.")
    if item.state != "scheduled" or item.starts_at <= timezone.now():
        raise ValidationError("Only future scheduled lessons can be canceled. Refresh the schedule.")
    policy = policy_for(item.series.profile)
    items = item.series.occurrences.filter(state="scheduled", starts_at__gte=item.starts_at)
    if scope == "one":
        items = items.filter(pk=item.pk)
    rows = []
    for current in items:
        outcome, label, credit = cancellation_outcome(current, policy, origin)
        rows.append({"item": current, "outcome": outcome, "label": label, "credit": credit})
    revision = digest({"policy": policy_snapshot(policy), "rows": [
        (r["item"].pk, r["item"].version, r["outcome"], r["credit"].version if r["credit"] else None) for r in rows]})
    return rows, revision


def cancellation_token(user, item, data, revision):
    return signing.dumps({"user": user.pk, "item": item.pk, "profile": item.series.profile_id,
        "payload": digest({k: data[k] for k in ["scope", "origin", "reason"]}), "revision": revision}, salt=CANCEL_SALT)


def validate_cancellation(user, item, data, revision, token):
    try:
        signed = signing.loads(token, salt=CANCEL_SALT, max_age=3600)
        if signed != {"user": user.pk, "item": item.pk, "profile": item.series.profile_id,
                "payload": digest(data), "revision": revision}:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError):
        raise ValidationError("The cancellation, policy or credit outcome changed. Preview again.")


def apply_cancellation(user, row, origin, reason, policy):
    """Called only inside cancel_lessons' tenant-locked transaction."""
    item, outcome, label, credit = (row[k] for k in ["item", "outcome", "label", "credit"])
    saved = credit.snapshot["policy"] if credit else policy_snapshot(policy)
    LessonCancellation.objects.create(occurrence=item, actor=user, origin=origin, reason=reason,
                                      outcome=label, policy_snapshot=saved)
    if outcome == "issued":
        credit = LessonCredit.objects.create(source=item, profile=item.series.profile,
            expires_at=item.ends_at + timedelta(days=policy.expiry_days),
            snapshot={**item.series.snapshot, "policy": saved})
    elif credit:
        credit.state = "available" if outcome == "restored" else "forfeited"
        credit.used_occurrence = None
        credit.version += 1
        credit.save(update_fields=["state", "used_occurrence", "version"])
    if credit:
        credit_record(user, credit, outcome, item, reason)


def credit_query(profile):
    return LessonCredit.objects.filter(profile=profile).select_related("source__series__service",
        "source__series__student__household", "source__series__profile", "used_occurrence__series", "profile")


def get_credit(profile, public_id):
    return get_object_or_404(credit_query(profile), public_id=public_id)


def redemption_plan(credit, cleaned):
    if not credit.available:
        raise ValidationError("This credit is no longer available. Refresh its history.")
    if not credit.source.series.service.is_active:
        raise ValidationError("The original lesson service is inactive. Reactivate it before redeeming this credit.")
    today = timezone.localdate(timezone=ZoneInfo(cleaned["location"].timezone_name))
    if not today <= cleaned["first_date"] <= today + timedelta(days=180):
        raise ValidationError("Choose a future make-up date within 180 days.")
    data = {**cleaned, "service": credit.source.series.service, "student": credit.source.series.student}
    rows = plan_dates(data, [datetime.combine(cleaned["first_date"], cleaned["start_time"])],
                      duration_minutes=credit.snapshot["duration_minutes"])
    if rows[0]["ends_at"] > credit.expires_at:
        raise ValidationError("The make-up lesson must finish before this credit expires.")
    return rows, digest({"plan": preview_revision(data, rows), "credit_version": credit.version,
                         "expires": credit.expires_at})


def action_payload(data, action):
    fields = RedeemCreditForm.base_fields if action == "redeem" else ["reason"]
    return digest({name: str(data.get(name, "")) for name in fields})


def action_token(user, credit, action, data, revision):
    return signing.dumps({"user": user.pk, "profile": credit.profile_id, "credit": credit.pk, "action": action,
        "key": str(uuid.uuid4()), "payload": action_payload(data, action), "revision": revision}, salt=CREDIT_SALT)


def read_action(user, credit, action, data, token):
    try:
        signed = signing.loads(token, salt=CREDIT_SALT, max_age=3600)
        key = uuid.UUID(signed["key"])
        if (signed["user"], signed["profile"], signed["credit"], signed["action"], signed["payload"]) != (
                user.pk, credit.profile_id, credit.pk, action, action_payload(data, action)):
            raise ValueError
        revision = signed["revision"]
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError("This credit form expired or changed. Reload and preview again.")
    return key, revision


@transaction.atomic
def redeem(user, slug, public_id, data, token):
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    credit = get_credit(profile, public_id)
    key, expected = read_action(user, credit, "redeem", data, token)
    previous = credit.events.filter(key=key, kind="redeemed").first()
    if previous:
        return previous.occurrence.series
    form = RedeemCreditForm(data, occurrence=credit.source)
    if not form.is_valid():
        raise ValidationError("The lesson setup changed. Review the fields and preview again.")
    rows, revision = redemption_plan(credit, form.cleaned_data)
    if revision != expected or rows[0]["state"] != "scheduled":
        raise ValidationError("The schedule or credit changed, or this time is unavailable. Preview again.")
    cleaned = form.cleaned_data
    teacher, location, room = (cleaned[k] for k in ["teacher", "location", "room"])
    series = LessonSeries.objects.create(profile=profile, service=credit.source.series.service,
        student=credit.source.series.student, teacher=teacher, location=location, room=room,
        first_date=cleaned["first_date"], last_date=cleaned["first_date"], start_time=cleaned["start_time"],
        submission_key=key, submission_digest=action_payload(data, "redeem"),
        snapshot={**credit.snapshot, "makeup_credit": str(credit.public_id),
            "teacher": teacher.name, "location": location.name, "room": room.name if room else "",
            "timezone": location.timezone_name, "buffer_minutes": teacher.buffer_minutes, "travel_minutes": teacher.travel_minutes})
    occurrence = LessonOccurrence.objects.create(series=series, **rows[0])
    credit.state, credit.used_occurrence = "redeemed", occurrence
    credit.version += 1
    credit.save(update_fields=["state", "used_occurrence", "version"])
    credit_record(user, credit, "redeemed", occurrence, cleaned["reason"], key)
    return series


@transaction.atomic
def revoke(user, slug, public_id, data):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    credit = get_credit(profile, public_id)
    form = RevokeCreditForm(data)
    if not form.is_valid():
        raise ValidationError("Enter a reason and confirm revocation.")
    # A revoke form binds the credit/version, while its reason is entered on submission.
    key, expected = read_action(user, credit, "revoke", {}, form.cleaned_data["revision"])
    previous = credit.events.filter(key=key, kind="revoked").first()
    if previous:
        if previous.reason != form.cleaned_data["reason"]:
            raise ValidationError("This revocation was already recorded with a different reason. Reload the credit history.")
        return credit
    if credit.state != "available" or credit.version != expected:
        raise ValidationError("Only an unused credit can be revoked. Cancel any booked make-up first, then refresh.")
    credit.state = "revoked"
    credit.version += 1
    credit.save(update_fields=["state", "version"])
    credit_record(user, credit, "revoked", None, form.cleaned_data["reason"], key)
    return credit


def validate_makeup_change(item, rows):
    credit = LessonCredit.objects.filter(used_occurrence=item).first()
    if credit and any(row["ends_at"] > credit.expires_at for row in rows):
        raise ValidationError("The make-up lesson must finish before its original credit expires.")


def move_redemption(user, original, replacement, reason):
    credit = LessonCredit.objects.filter(used_occurrence_id=original).first()
    if credit:
        credit.used_occurrence = replacement
        credit.version += 1
        credit.save(update_fields=["used_occurrence", "version"])
        credit_record(user, credit, "moved", replacement, reason)
