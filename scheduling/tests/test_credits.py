from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.test import Client
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder
from scheduling import credits, lessons
from scheduling.credit_forms import CreditPolicyForm, RedeemCreditForm
from scheduling.credit_models import LessonCancellation, LessonCredit, LessonCreditEvent, LessonCreditPolicy
from scheduling.lesson_models import LessonClosure, LessonSeries
from scheduling.models import Location, Room, SchedulingProfile
from scheduling.tests.test_lessons import music, book, url
from scheduling.tests.test_lesson_changes import change_data, move, preview as change_preview

pytestmark = pytest.mark.django_db


def enable(m, **changes):
    return LessonCreditPolicy.objects.create(profile=m["profile"], enabled=True, **changes)


def cancel_preview(m, item, scope="one", origin="family", reason="Family requested cancellation"):
    data = dict(scope=scope, origin=origin, reason=reason)
    rows, revision = credits.cancellation_plan(item, scope, origin)
    return data, credits.cancellation_token(m["user"], item, data, revision), rows


def cancel(m, item, **changes):
    data, token, _ = cancel_preview(m, item, **changes)
    return lessons.cancel_lessons(m["user"], m["slug"], item.pk, **data, token=token)


def issued(m, **policy):
    enable(m, **policy)
    source = book(m).occurrences.first()
    cancel(m, source)
    return credits.get_credit(m["profile"], LessonCredit.objects.get().public_id)


def redemption(m, credit, **changes):
    data = {"first_date": "2030-01-08", "start_time": "11:00", "teacher": str(m["teacher"].pk),
            "location": str(m["location"].pk), "room": str(m["room"].pk), "reason": "Make-up agreed", **changes}
    form = RedeemCreditForm(data, occurrence=credit.source)
    assert form.is_valid(), form.errors
    rows, revision = credits.redemption_plan(credit, form.cleaned_data)
    return data, credits.action_token(m["user"], credit, "redeem", data, revision), rows


def redeem(m, credit, **changes):
    data, token, _ = redemption(m, credit, **changes)
    return credits.redeem(m["user"], m["slug"], credit.public_id, data, token)


def revoke_data(m, credit):
    return {"reason": "Issued in error", "confirm": "on",
            "revision": credits.action_token(m["user"], credit, "revoke", {}, credit.version)}


def test_defaults_off_policy_save_and_stale_first_create(music):
    m = music
    original = credits.policy_form(m["profile"])
    assert not original.instance.enabled and not LessonCreditPolicy.objects.exists()
    data = dict(enabled="on", family_cancellations="on", studio_cancellations="on", notice_hours=48,
                expiry_days=60, edit_revision=original["edit_revision"].value())
    assert credits.save_policy(m["user"], m["slug"], data).is_valid()
    assert not credits.save_policy(m["user"], m["slug"], data).is_valid()
    policy = LessonCreditPolicy.objects.get()
    assert policy.version == 1 and policy.notice_hours == 48
    edited = credits.policy_form(m["profile"])
    data.update(expiry_days=30, edit_revision=edited["edit_revision"].value())
    assert credits.save_policy(m["user"], m["slug"], data).is_valid()
    assert not credits.save_policy(m["user"], m["slug"], data).is_valid()
    assert AdminAuditLog.objects.filter(model_label="scheduling.lessoncreditpolicy").count() == 2


@pytest.mark.parametrize("field,value", [("notice_hours", -1), ("notice_hours", 721), ("expiry_days", 0), ("expiry_days", 366)])
def test_policy_bounds(music, field, value):
    m = music
    data = dict(notice_hours=24, expiry_days=90, **{})
    data[field] = value
    assert not CreditPolicyForm(data).is_valid()
    policy = enable(m)
    setattr(policy, field, value)
    with pytest.raises(ValidationError):
        policy.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        policy.save()


@pytest.mark.parametrize("origin,enabled,qualifies,hours,expected", [
    ("family", False, True, 100, False), ("family", True, False, 100, False),
    ("studio", True, False, 1, False), ("studio", True, True, 1, True),
    ("family", True, True, 24, True), ("family", True, True, 23.99, False)])
def test_cancellation_eligibility_boundaries(music, monkeypatch, origin, enabled, qualifies, hours, expected):
    m = music
    policy = enable(m)
    policy.enabled = enabled
    setattr(policy, f"{origin}_cancellations", qualifies)
    policy.save()
    item = book(m).occurrences.first()
    now = item.starts_at - timedelta(hours=hours)
    monkeypatch.setattr(timezone, "now", lambda: now)
    cancel(m, item, origin=origin)
    assert LessonCredit.objects.exists() == expected
    outcome = LessonCancellation.objects.get()
    assert outcome.origin == origin and outcome.policy_snapshot["enabled"] == enabled
    assert not PaymentOrder.objects.exists()


def test_cancellation_preview_no_effect_confirm_atomic_idempotent(music):
    m = music
    enable(m)
    series = book(m)
    item = series.occurrences.first()
    data, token, rows = cancel_preview(m, item, scope="future")
    assert len(rows) == 3 and not LessonCredit.objects.exists()
    for _ in range(2):
        lessons.cancel_lessons(m["user"], m["slug"], item.pk, **data, token=token)
    assert LessonCredit.objects.count() == LessonCancellation.objects.count() == LessonCreditEvent.objects.count() == 3
    credit = LessonCredit.objects.first()
    assert credit.available and credit.status_label == "Available"
    assert credit.expires_at == credit.source.ends_at + timedelta(days=90)
    assert credit.snapshot["policy"]["notice_hours"] == 24
    assert not PaymentOrder.objects.exists()


@pytest.mark.parametrize("defect", ["missing", "bad", "expired", "policy", "reason", "origin", "late", "schedule"])
def test_changed_cancellation_preview_blocks_every_write(music, monkeypatch, defect):
    m = music
    policy = enable(m)
    item = book(m).occurrences.first()
    data, token, _ = cancel_preview(m, item)
    if defect == "missing": token = None
    if defect == "bad": token = "bad"
    if defect == "expired":
        monkeypatch.setattr(signing.TimestampSigner, "timestamp", lambda self: "1")
        data, token, _ = cancel_preview(m, item)
    if defect == "policy": policy.expiry_days = 5; policy.save()
    if defect == "reason": data["reason"] = "Changed"
    if defect == "origin": data["origin"] = "studio"
    if defect == "schedule": item.version += 1; item.save()
    if defect == "late":
        now = item.starts_at - timedelta(hours=1)
        monkeypatch.setattr(timezone, "now", lambda: now)
    with pytest.raises(ValidationError):
        lessons.cancel_lessons(m["user"], m["slug"], item.pk, **data, token=token)
    item.refresh_from_db()
    assert item.state == "scheduled" and not LessonCredit.objects.exists() and not LessonCancellation.objects.exists()


@pytest.mark.parametrize("scope,origin", [("bad", "family"), ("one", "bad")])
def test_invalid_cancel_plan(music, scope, origin):
    with pytest.raises(ValidationError):
        credits.cancellation_plan(book(music).occurrences.first(), scope, origin)


def test_redeem_keeps_identity_duration_history_and_retry_never_reserves_again(music):
    m = music
    credit = issued(m)
    m["service"].duration_minutes = 60
    m["service"].name = "Renamed Piano"
    m["service"].save()
    credit = credits.get_credit(m["profile"], credit.public_id)
    data, token, rows = redemption(m, credit)
    assert rows[0]["ends_at"] - rows[0]["starts_at"] == timedelta(minutes=30)
    result = credits.redeem(m["user"], m["slug"], credit.public_id, data, token)
    assert result.student == m["student"] and result.service == m["service"] and result.snapshot["service"] == "Piano"
    assert result.occurrences.count() == 1
    assert result.snapshot["makeup_credit"] == str(credit.public_id)
    credit.refresh_from_db()
    assert not credit.available and credit.status_label == "Booked"
    cancel(m, result.occurrences.get())
    credit.refresh_from_db()
    assert credit.available
    assert credits.redeem(m["user"], m["slug"], credit.public_id, data, token) == result
    credit.refresh_from_db()
    assert credit.available and LessonSeries.objects.count() == 2
    assert list(credit.events.values_list("kind", flat=True)) == ["issued", "redeemed", "restored"]
    assert not PaymentOrder.objects.exists()


@pytest.mark.parametrize("origin,hours,expected", [("family", 24, "available"), ("family", 23, "forfeited"), ("studio", 1, "available")])
def test_makeup_cancellation_restores_or_forfeits_same_credit(music, monkeypatch, origin, hours, expected):
    m = music
    credit = issued(m)
    result = redeem(m, credit)
    item = result.occurrences.get()
    now = item.starts_at - timedelta(hours=hours)
    monkeypatch.setattr(timezone, "now", lambda: now)
    # New policy changes cannot change an already issued credit's cancellation rules.
    LessonCreditPolicy.objects.filter(profile=m["profile"]).update(enabled=False, notice_hours=720, expiry_days=365)
    cancel(m, item, origin=origin)
    fresh = LessonCredit.objects.get()
    assert fresh.state == expected and fresh.expires_at == credit.expires_at
    assert fresh.used_occurrence is None and fresh.events.count() == 3


def test_redeem_existing_credit_after_policy_disabled_and_multiple_restore_cycles(music):
    m = music
    credit = issued(m)
    LessonCreditPolicy.objects.filter(profile=m["profile"]).update(enabled=False)
    for _ in range(3):
        credit.refresh_from_db()
        result = redeem(m, credit)
        cancel(m, result.occurrences.get())
    assert LessonCredit.objects.count() == 1 and credit.events.count() == 7


def test_expiry_blocks_booking_even_if_reservation_is_made_before_expiry(music, monkeypatch):
    m = music
    credit = issued(m, expiry_days=1)
    with pytest.raises(ValidationError, match="finish"):
        redemption(m, credit, first_date="2030-01-10")
    with pytest.raises(ValidationError, match="180"):
        redemption(m, credit, first_date="2031-01-10")
    now = credit.expires_at
    monkeypatch.setattr(timezone, "now", lambda: now)
    assert credit.status_label == "Expired" and not credit.available
    with pytest.raises(ValidationError, match="available"):
        redemption(m, credit)


def test_used_credit_and_attendance_do_not_issue_a_new_credit(music, monkeypatch):
    from scheduling.lesson_changes import attendance_token, record_attendance
    m = music
    credit = issued(m)
    item = redeem(m, credit).occurrences.get()
    now = item.ends_at + timedelta(seconds=1)
    monkeypatch.setattr(timezone, "now", lambda: now)
    record_attendance(m["user"], m["slug"], item.pk, {"attendance": "no_show", "reason": "Not here",
        "revision": attendance_token(m["user"], item)})
    credit.refresh_from_db()
    assert credit.status_label == "Used" and LessonCredit.objects.count() == 1
    with pytest.raises(ValidationError): cancel(m, item)


def test_makeup_reschedule_transfers_redemption_and_enforces_original_expiry(music):
    m = music
    credit = issued(m, expiry_days=7)
    original = redeem(m, credit).occurrences.get()
    with pytest.raises(ValidationError, match="expires"):
        change_preview(m, original, change_data(original, first_date="2030-01-20"))
    replacement = move(m, original, change_data(original, first_date="2030-01-09"))
    credit.refresh_from_db()
    assert credit.used_occurrence.series == replacement
    assert list(credit.events.values_list("kind", flat=True)) == ["issued", "redeemed", "moved"]
    cancel(m, replacement.occurrences.get())
    credit.refresh_from_db()
    assert credit.available and credit.events.last().kind == "restored"
    assert not PaymentOrder.objects.exists()


@pytest.mark.parametrize("defect", ["bad", "expired", "identity", "payload", "version", "conflict", "closure", "teacher", "inactive", "setup"])
def test_redemption_revalidates_under_lock_and_keeps_credit(music, monkeypatch, defect):
    m = music
    credit = issued(m)
    data, token, _ = redemption(m, credit)
    if defect == "bad": token = "bad"
    if defect == "expired":
        monkeypatch.setattr(signing.TimestampSigner, "timestamp", lambda self: "1")
        data, token, _ = redemption(m, credit)
    if defect == "identity":
        other = get_user_model().objects.create_user(username="different-staff")
        signed = signing.loads(token, salt=credits.CREDIT_SALT); signed["user"] = other.pk
        token = signing.dumps(signed, salt=credits.CREDIT_SALT)
    if defect == "payload": data["first_date"] = "2030-01-09"
    if defect == "version": LessonCredit.objects.filter(pk=credit.pk).update(version=2)
    if defect == "conflict": book(m, {**m["data"], "first_date": "2030-01-08", "last_date": "2030-01-08", "start_time": "11:00"})
    if defect == "closure": LessonClosure.objects.create(location=m["location"], first_date=date(2030,1,8), last_date=date(2030,1,8), reason="Closed")
    if defect == "teacher": m["teacher"].services.clear()
    if defect == "inactive": m["service"].is_active=False; m["service"].save()
    if defect == "setup": m["teacher"].buffer_minutes=20; m["teacher"].save()
    with pytest.raises(ValidationError): credits.redeem(m["user"], m["slug"], credit.public_id, data, token)
    credit.refresh_from_db()
    assert credit.available and credit.events.count() == 1


def test_revoke_idempotency_wrong_reason_expired_and_redeemed_guards(music):
    m = music
    credit = issued(m)
    data = revoke_data(m, credit)
    credits.revoke(m["user"], m["slug"], credit.public_id, data)
    credits.revoke(m["user"], m["slug"], credit.public_id, data)
    assert LessonCredit.objects.get().status_label == "Revoked" and credit.events.count() == 2
    with pytest.raises(ValidationError, match="different reason"):
        credits.revoke(m["user"], m["slug"], credit.public_id, {**data,"reason":"Different"})
    with pytest.raises(ValidationError): credits.revoke(m["user"], m["slug"], credit.public_id, {})
    with pytest.raises(ValidationError): credits.revoke(m["user"], m["slug"], credit.public_id, {**data,"revision":"bad"})
    with pytest.raises(ValidationError): redemption(m, LessonCredit.objects.get())


def test_revoke_cannot_invalidate_a_booked_lesson_or_stale_state(music):
    m = music
    credit = issued(m)
    stale = revoke_data(m, credit)
    result = redeem(m, credit)
    with pytest.raises(ValidationError, match="unused"):
        credits.revoke(m["user"], m["slug"], credit.public_id, stale)
    cancel(m, result.occurrences.get())
    with pytest.raises(ValidationError, match="unused"):
        credits.revoke(m["user"], m["slug"], credit.public_id, stale)


def test_database_constraints_unique_credit_and_event(music):
    m = music
    credit = issued(m)
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonCredit.objects.create(source=credit.source, profile=m["profile"], expires_at=credit.expires_at)
    for values in [{"state":"unknown"}, {"state":"redeemed"}, {"used_occurrence":credit.source}]:
        with pytest.raises(IntegrityError), transaction.atomic():
            LessonCredit.objects.filter(pk=credit.pk).update(**values)
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonCreditEvent.objects.create(credit=credit, kind="bad")
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonCreditEvent.objects.create(credit=credit, kind="issued", key=credit.events.first().key)


@pytest.mark.parametrize("operation", ["redeem", "revoke", "cancel"])
@pytest.mark.django_db(transaction=True)
def test_concurrent_credit_mutations_have_one_effect(music, operation):
    m = music
    credit = issued(m)
    data, token, _ = redemption(m, credit)
    # Distinct previews must not spend the same entitlement twice.
    _, token2, _ = redemption(m, credit)
    if operation == "cancel":
        item = credits.redeem(m["user"], m["slug"], credit.public_id, data, token).occurrences.get()
        cancel_data, cancel_token, _ = cancel_preview(m, item)
    def run(i):
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=m["user"].pk)
            if operation == "redeem":
                credits.redeem(actor, m["slug"], credit.public_id, data, [token,token2][i])
            elif operation == "revoke":
                credits.revoke(actor, m["slug"], credit.public_id, revoke_data(m, credit))
            else:
                lessons.cancel_lessons(actor, m["slug"], item.pk, **cancel_data, token=cancel_token)
            return "ok"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0,1]))
    assert results.count("ok") == (2 if operation == "cancel" else 1)
    credit.refresh_from_db()
    kind = {"redeem":"redeemed", "revoke":"revoked", "cancel":"restored"}[operation]
    assert credit.events.filter(kind=kind).count() == 1


def test_cancellation_rollback_includes_all_credits_and_original_lessons(music):
    m = music
    enable(m)
    series = book(m)
    original = credits.credit_record
    def fail_second(*args, **kwargs):
        if LessonCreditEvent.objects.exists(): raise ValidationError("Second credit failed")
        return original(*args, **kwargs)
    with patch("scheduling.credits.credit_record", side_effect=fail_second), pytest.raises(ValidationError):
        cancel(m, series.occurrences.first(), scope="future")
    assert not LessonCredit.objects.exists() and not LessonCancellation.objects.exists()
    assert series.occurrences.filter(state="scheduled").count() == 3


def test_redemption_rollback_cannot_leave_a_lesson_without_its_credit_event(music):
    m = music
    credit = issued(m)
    data, token, _ = redemption(m, credit)
    count = LessonSeries.objects.count()
    with patch("scheduling.credits.credit_record", side_effect=ValidationError("Audit unavailable")), pytest.raises(ValidationError):
        credits.redeem(m["user"], m["slug"], credit.public_id, data, token)
    credit.refresh_from_db()
    assert credit.available and credit.events.count() == 1 and LessonSeries.objects.count() == count


@pytest.mark.django_db(transaction=True)
def test_distinct_credits_competing_for_one_room_do_not_double_book(music):
    m = music
    enable(m)
    series = book(m)
    cancel(m, series.occurrences.first(), scope="future")
    first, second = list(credits.credit_query(m["profile"]))[:2]
    requests = []
    for credit in [first, second]:
        data, token, _ = redemption(m, credit)
        requests.append((credit.public_id, data, token))
    def run(request):
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=m["user"].pk)
            credits.redeem(actor, m["slug"], *request)
            return "ok"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))
    assert sorted(results) == ["ok", "rejected"]
    assert LessonCredit.objects.filter(state="redeemed").count() == 1
    assert LessonCreditEvent.objects.filter(kind="redeemed").count() == 1


@pytest.mark.django_db(transaction=True)
def test_revoke_racing_redemption_cannot_leave_an_unfunded_makeup(music):
    m = music
    credit = issued(m)
    data, token, _ = redemption(m, credit)
    revoke_request = revoke_data(m, credit)
    def run(operation):
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=m["user"].pk)
            if operation == "redeem":
                credits.redeem(actor, m["slug"], credit.public_id, data, token)
            else:
                credits.revoke(actor, m["slug"], credit.public_id, revoke_request)
            return "ok"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["redeem", "revoke"]))
    assert sorted(results) == ["ok", "rejected"]
    credit.refresh_from_db()
    assert credit.events.count() == 2
    assert LessonSeries.objects.count() == (2 if credit.state == "redeemed" else 1)
