from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import uuid

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder
from scheduling import family_access as access
from scheduling.family_models import FamilyScheduleGrant
from scheduling.lesson_models import LessonHousehold, LessonStudent
from scheduling.models import SchedulingProfile
from scheduling.tests.test_lessons import music

pytestmark = pytest.mark.django_db


def invitation(m, **changes):
    form = access.InviteForm(user=m["user"], household=m["family"])
    return {"recipient_name": "Riley's guardian", "recipient_email": "guardian@example.invalid",
        "students": [str(m["student"].pk)], "days": "7", "verified": "on",
        "invite_key": form["invite_key"].value(), **changes}


def issue(m, **changes):
    return access.issue_invitation(m["user"], m["slug"], m["family"].pk, invitation(m, **changes))


def opened(m):
    grant, secret = issue(m)
    session = SessionStore()
    access.redeem_invitation(m["slug"], secret, session)
    session.save()
    grant.refresh_from_db()
    return grant, secret, session


def test_explicit_scope_hashes_audit_and_idempotent_create(music):
    m = music
    sibling = LessonStudent.objects.create(household=m["family"], name="Sibling")
    data = invitation(m)
    grant, token = access.issue_invitation(m["user"], m["slug"], m["family"].pk, data)
    assert grant.students.count() == 1 and not grant.students.filter(pk=sibling.pk).exists()
    assert grant.token_hash == access.digest(token) and token not in str(grant.__dict__)
    assert grant.status_label == "Not opened" and grant.verified_by == m["user"]
    assert grant.token_expires_at == timezone.now()+timedelta(days=1)
    assert grant.expires_at == timezone.now()+timedelta(days=7)
    assert access.issue_invitation(m["user"],m["slug"],m["family"].pk,data) == (grant,None)
    with pytest.raises(ValidationError, match="already used"):
        access.issue_invitation(m["user"],m["slug"],m["family"].pk,{**data,"days":"30"})
    assert FamilyScheduleGrant.objects.count() == AdminAuditLog.objects.count() == 1
    assert token not in str(list(AdminAuditLog.objects.values()))
    assert not PaymentOrder.objects.exists()


@pytest.mark.parametrize("changes", [{"verified":""},{"students":[]},{"students":["9999"]},
    {"days":"31"},{"recipient_email":"bad"},{"recipient_name":""},{"invite_key":"bad"},
    {"recipient_email":"x"*64+"@"+".".join(["a"*63]*3)+".com"}])
def test_bad_or_unverified_invites_rejected(music, changes):
    with pytest.raises(ValidationError):
        issue(music, **changes)
    assert not FamilyScheduleGrant.objects.exists()


@pytest.mark.parametrize("payload", [{}, {"user":1}, {"key":"bad"},
    {"user":0,"profile":0,"household":0,"key":str(uuid.uuid4())}])
def test_forged_invite_scope_and_shape(music,payload):
    with pytest.raises(ValidationError, match="expired"):
        issue(music, invite_key=signing.dumps(payload,salt=access.SALT))


def test_invite_age_and_foreign_students(music):
    m = music
    data = invitation(m)
    with patch("django.core.signing.time.time", return_value=9999999999):
        with pytest.raises(ValidationError, match="expired"):
            access.issue_invitation(m["user"],m["slug"],m["family"].pk,data)
    other = LessonHousehold.objects.create(profile=m["profile"],name="Other",email=m["family"].email)
    child = LessonStudent.objects.create(household=other,name="Other child")
    with pytest.raises(ValidationError):
        issue(m,students=[str(child.pk)])


def test_one_use_link_rotates_session_and_never_grants_staff(music):
    m = music
    session = SessionStore(); session["existing"] = "preserved"; session.save()
    old = session.session_key
    grant, token = issue(m)
    access.redeem_invitation(m["slug"],token,session)
    assert old != session.session_key and session["existing"] == "preserved"
    assert "_auth_user_id" not in session
    assert access.family_grant(m["slug"],session)[1] == grant
    for attempt in [session, SessionStore()]:
        with pytest.raises(ValidationError, match="unavailable"):
            access.redeem_invitation(m["slug"],token,attempt)
    grant.refresh_from_db()
    assert grant.status_label == "Active" and grant.session_hash != token


@pytest.mark.parametrize("token",["", "x"*43, "x"*10000, "with/slashes", "☃"*43])
def test_invalid_link_and_session_are_uniform(music,token):
    with pytest.raises(ValidationError, match="unavailable"):
        access.redeem_invitation(music["slug"],token,SessionStore())
    session = SessionStore(); session[access.SESSION_KEY] = {str(music["profile"].pk):token}
    with pytest.raises(Http404):
        access.family_grant(music["slug"],session)


def test_expiry_boundary_and_owner_revocation(music,monkeypatch):
    m = music
    grant, token = issue(m)
    original = timezone.now()
    monkeypatch.setattr(timezone,"now",lambda:original+timedelta(days=1))
    assert grant.status_label == "Link expired"
    with pytest.raises(ValidationError):
        access.redeem_invitation(m["slug"],token,SessionStore())
    monkeypatch.setattr(timezone,"now",lambda:original)
    grant, token, session = opened(m)
    monkeypatch.setattr(timezone,"now",lambda:grant.expires_at)
    assert grant.status_label == "Expired"
    with pytest.raises(Http404):
        access.family_grant(m["slug"],session)
    monkeypatch.setattr(timezone,"now",lambda:original)
    data = {"reason":"Guardian authorization removed","confirm":"on"}
    with pytest.raises(ValidationError):
        access.revoke_access(m["user"],m["slug"],grant.public_id,{})
    access.revoke_access(m["user"],m["slug"],grant.public_id,data)
    count = AdminAuditLog.objects.count()
    access.revoke_access(m["user"],m["slug"],grant.public_id,data)
    assert AdminAuditLog.objects.count() == count
    grant.refresh_from_db(); assert grant.status_label == "Revoked"
    with pytest.raises(Http404):
        access.family_grant(m["slug"],session)


def test_close_access_blocks_copied_session_without_logging_staff_out(music):
    m = music
    grant, _, session = opened(m)
    copied = SessionStore(); copied.update(dict(session))
    session["_auth_user_id"] = "existing-staff"
    session[access.SESSION_KEY]["other-studio"] = "another-session"
    access.close_family_access(m["slug"],session)
    assert session["_auth_user_id"] == "existing-staff"
    assert session[access.SESSION_KEY] == {"other-studio":"another-session"}
    count = AdminAuditLog.objects.count()
    access.close_family_access(m["slug"],copied)
    access.close_family_access(m["slug"],SessionStore())
    assert AdminAuditLog.objects.count() == count
    with pytest.raises(Http404):
        access.family_grant(m["slug"],copied)
    grant.refresh_from_db(); assert grant.revoked_at


def test_new_sibling_moved_child_removed_scope_and_tenant_isolation(music):
    m = music
    grant, _, session = opened(m)
    sibling = LessonStudent.objects.create(household=m["family"],name="New sibling")
    assert list(access.authorized_students(grant)) == [m["student"]]
    school = School.objects.create(slug="elsewhere",display_name="Elsewhere")
    profile = SchedulingProfile.objects.create(school=school,enabled=True,lessons_enabled=True)
    other = LessonHousehold.objects.create(profile=profile,name="Other",email=m["family"].email)
    session[access.SESSION_KEY][str(profile.pk)] = session[access.SESSION_KEY][str(m["profile"].pk)]
    with pytest.raises(Http404):
        access.family_grant(school.slug,session)
    with pytest.raises(Http404):
        access.issue_invitation(m["user"],m["slug"],other.pk,invitation(m))
    m["student"].household = other; m["student"].save()
    assert not access.authorized_students(grant).exists()
    with pytest.raises(Http404):
        access.family_grant(m["slug"],session)
    pending, secret = issue(m,students=[str(sibling.pk)])
    pending.students.clear()
    with pytest.raises(ValidationError):
        access.redeem_invitation(m["slug"],secret,SessionStore())


@pytest.mark.parametrize("role",["scheduler","viewer"])
def test_only_owners_grant_or_revoke(music,role):
    m = music
    grant,_ = issue(m)
    m["member"].role=role; m["member"].save()
    with pytest.raises(Http404):
        issue(m)
    with pytest.raises(Http404):
        access.revoke_access(m["user"],m["slug"],grant.public_id,{"reason":"test","confirm":"on"})


@pytest.mark.parametrize("field",["enabled","lessons_enabled"])
def test_module_gates_checked_on_every_read_and_exchange(music,field):
    m = music
    _,_,session = opened(m)
    _,token = issue(m)
    setattr(m["profile"],field,False); m["profile"].save()
    with pytest.raises(Http404): access.family_grant(m["slug"],session)
    with pytest.raises(Http404): access.redeem_invitation(m["slug"],token,SessionStore())


def test_atomic_failure_never_leaves_grant_or_redeemed_token(music):
    m = music
    with patch.object(access,"audit",side_effect=RuntimeError):
        with pytest.raises(RuntimeError): issue(m)
    assert not FamilyScheduleGrant.objects.exists()
    grant,secret = issue(m)
    with patch.object(access,"audit",side_effect=RuntimeError):
        with pytest.raises(RuntimeError): access.redeem_invitation(m["slug"],secret,SessionStore())
    grant.refresh_from_db(); assert grant.redeemed_at is None
    access.redeem_invitation(m["slug"],secret,SessionStore())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation",["issue","redeem","revoke-vs-open"])
def test_concurrent_invite_operations(music,operation):
    m = music
    data = invitation(m)
    grant, token = issue(m)
    def run(i):
        close_old_connections()
        try:
            if operation == "issue":
                return access.issue_invitation(m["user"],m["slug"],m["family"].pk,data)[0].pk
            if operation == "revoke-vs-open" and i == 1:
                access.revoke_access(m["user"],m["slug"],grant.public_id,{"reason":"Revoke","confirm":"on"})
                return None
            session=SessionStore()
            try: access.redeem_invitation(m["slug"],token,session)
            except ValidationError: return None
            return session
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[0,1]))
    if operation == "issue":
        assert results[0] == results[1] and FamilyScheduleGrant.objects.count() == 2
    elif operation == "redeem":
        assert sum(r is not None for r in results) == 1
    else:
        grant.refresh_from_db(); assert grant.revoked_at
        for result in results:
            if result:
                with pytest.raises(Http404): access.family_grant(m["slug"],result)


@pytest.mark.parametrize("changes",[{"expires_at":timezone.now()-timedelta(days=9999)},
    {"session_hash":"hash"},{"token_expires_at":timezone.now()+timedelta(days=9999)}])
def test_database_rejects_invalid_access_dates_and_session(music,changes):
    grant,_=issue(music)
    with pytest.raises(IntegrityError), transaction.atomic():
        FamilyScheduleGrant.objects.filter(pk=grant.pk).update(**changes)
