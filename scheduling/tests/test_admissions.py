from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import DraftSubmission, Lead, School, SchoolAdminMembership, Submission
from scheduling import admissions, visits
from scheduling.admission_forms import PolicyForm
from scheduling.admission_gates import application_gate, lead_application_gate
from scheduling.capabilities import validate_removal
from scheduling.models import AdmissionCase, AdmissionPolicy, Offering, SchedulingMembership, SchedulingProfile, Session, VisitBooking, VisitProgram, VisitSlot
from .test_visits import tour, book, booking_data, post_data, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def workflow(tour):
    profile = tour.host.profile
    school = profile.school
    school.slug = "preschool-demo"
    school.plan = "custom"
    school.feature_flags = {"email_notifications_enabled": False, "ai_summary_enabled": False}
    school.save()
    user = tour.host.user
    user.is_staff = True
    user.save()
    SchoolAdminMembership.objects.create(user=user, school=school, role="owner")
    tour.program.capture_leads = True
    tour.program.save()
    policy = AdmissionPolicy.objects.create(profile=profile, enabled=True, eligible_statuses=["New", "In Review"])
    offering = Offering.objects.create(location=tour.program.offering.location, name="Classroom playdate", is_published=True)
    program = VisitProgram.objects.create(offering=offering, kind="playdate")
    start = tour.session.starts_at + timedelta(days=1)
    session = Session.objects.create(offering=offering, room=tour.session.room, capacity=4, starts_at=start,
        ends_at=start+timedelta(minutes=30), is_published=True)
    slot = VisitSlot.objects.create(program=program, session=session, host=tour.host)
    return SimpleNamespace(tour=tour, slot=slot, profile=profile, school=school, user=user, policy=policy)


def core_url(w, name, **kwargs):
    return reverse(name, kwargs={"school_slug": w.school.slug, **kwargs})


def completed_tour(w):
    item = book(w.tour)
    Session.objects.filter(pk=w.tour.session_id).update(starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()-timedelta(minutes=30))
    visits.change_state(w.user, w.school.slug, item.public_id, "attended", "booked")
    case = admissions.case_for_lead(w.policy, item.lead, create=True)
    return case


def submitted_case(w, client=None):
    client = client or Client()
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    response = client.get(core_url(w, "apply_resume", token=draft.token), follow=True)
    assert response.status_code == 200
    response = client.post(core_url(w, "apply"), {"student_name": "Sam", "guardian_name": "Taylor Example",
        "contact_email": "taylor@example.invalid", "_draft_token": draft.token})
    assert response.status_code == 302, response.content[:2000]
    case.refresh_from_db()
    assert case.submission_id
    return case


def eligible_case(w):
    case = submitted_case(w)
    case.playdate_released_at = timezone.now()
    case.save()
    return case


def playdate_data(w, case, **changes):
    return booking_data(w.slot, **{"booking_token": visits.booking_token(w.slot, "browser-a", case=case), **changes})


def reserve(w, case, **changes):
    item, form = visits.book(w.school.slug, w.slot.pk, playdate_data(w, case, **changes), "browser-a", case=case)
    assert item is not None, form.errors
    return item, form


def test_application_enforced_before_and_after_attendance(workflow, client):
    w = workflow
    item = book(w.tour)
    draft = DraftSubmission.objects.create(school=w.school, lead=item.lead, email=item.lead.email)
    for method in [client.get, client.post]:
        for name, kwargs in [("apply", {}), ("apply_resume", {"token": draft.token}),
            ("apply_payment", {"draft_token": draft.token}), ("apply_payment_confirm", {"draft_token": draft.token}),
            ("apply_payment_bypass", {"draft_token": draft.token})]:
            response = method(core_url(w, name, **kwargs))
            if name == "apply_payment_bypass" and method == client.get:
                assert response.status_code == 405
                continue
            assert response.status_code == 403
            assert "no-store" in response["Cache-Control"]
    assert not AdmissionCase.objects.exists() and not Submission.objects.exists()
    Session.objects.filter(pk=w.tour.session_id).update(starts_at=timezone.now()-timedelta(minutes=10))
    visits.change_state(w.user, w.school.slug, item.public_id, "attended", "booked")
    assert client.get(core_url(w, "apply_resume", token=draft.token), follow=True).status_code == 200
    assert AdmissionCase.objects.get().draft == draft
    response = client.post(core_url(w, "apply"), {"student_name": "Sam", "guardian_name": "Taylor",
        "contact_email": "taylor@example.invalid"})
    case = AdmissionCase.objects.get()
    assert response.url == core_url(w, "family_status", token=case.submission.status_token)
    item.lead.refresh_from_db()
    assert item.lead.converted_submission == case.submission
    repeat = client.get(core_url(w, "apply_resume", token=draft.token))
    assert repeat.url == response.url and "no-store" in repeat["Cache-Control"]
    assert Submission.objects.count() == 1


def test_staff_application_release_and_lead_entry_points(workflow, client):
    w = workflow
    case = completed_tour(w)
    w.policy.application_release = "staff"
    w.policy.save()
    client.force_login(w.user)
    detail = core_url(w, "school_lead_detail", lead_id=case.lead_id)
    assert not client.get(detail).context["booking_admissions"]["allowed"]
    assert not DraftSubmission.objects.exists()
    for name in ["school_lead_start_enrollment", "school_lead_resend_resume_link"]:
        assert client.post(core_url(w, name, lead_id=case.lead_id)).url == detail
    path = url(w.tour, "admission_case", lead_pk=case.lead_id)
    assert client.get(path).status_code == 200
    assert client.post(path, {"action": "release_application", "revision": ""}).status_code == 302
    case.refresh_from_db()
    assert case.application_released_at
    assert client.get(detail).context["booking_admissions"]["allowed"]
    assert DraftSubmission.objects.count() == 1
    assert client.post(core_url(w, "school_lead_start_enrollment", lead_id=case.lead_id)).status_code == 302
    assert b"changed" in client.post(path, {"action": "release_application", "revision": ""}, follow=True).content
    assert client.post(path, {"action": "wrong"}).status_code == 404


def test_tour_receipt_application_flow_is_browser_bound(workflow, client):
    w = workflow
    path = url(w.tour, "visit_book", pk=w.tour.pk)
    token = client.get(path).context["booking_token"]
    receipt = client.post(path, booking_data(w.tour, booking_token=token)).url
    item = VisitBooking.objects.get()
    start = url(w.tour, "visit_application", public_id=item.public_id)
    assert b"Complete your tour" in client.get(receipt).content
    assert client.get(start).status_code == 405
    assert Client().post(start).status_code == 404
    assert client.post(start).status_code == 403
    Session.objects.filter(pk=w.tour.session_id).update(starts_at=timezone.now()-timedelta(minutes=10))
    visits.change_state(w.user, w.school.slug, item.public_id, "attended", "booked")
    assert b"Start application" in client.get(receipt).content
    response = client.post(start)
    assert response.status_code == 302 and "/apply/resume/" in response.url
    draft = DraftSubmission.objects.get()
    assert client.get(response.url, follow=True).status_code == 200
    client.post(core_url(w, "apply"), {"student_name": "Sam", "guardian_name": "Taylor", "contact_email": item.contact_email})
    assert client.post(start).url == core_url(w, "family_status", token=AdmissionCase.objects.get().submission.status_token)
    assert b"View application status" in client.get(receipt).content
    assert DraftSubmission.objects.get().pk == draft.pk


def test_playdate_http_flow_preserves_identity_and_records_status(workflow, client):
    w = workflow
    case = submitted_case(w, client)
    path = url(w.tour, "playdates", token=case.submission.status_token)
    book_path = url(w.tour, "playdate_book", token=case.submission.status_token, pk=w.slot.pk)
    assert b"let you know" in client.get(path).content
    assert client.get(book_path).status_code == 403
    client.force_login(w.user)
    release = url(w.tour, "admission_case", lead_pk=case.lead_id)
    client.post(release, {"action": "release_playdate", "revision": ""})
    assert b"Classroom playdate" in client.get(path).content
    status_url = core_url(w, "family_status", token=case.submission.status_token)
    assert b"Choose a playdate" in client.get(status_url).content
    first = client.get(book_path)
    data = playdate_data(w, case, booking_token=first.context["booking_token"], children="0")
    assert client.post(book_path, data).status_code == 400
    data.update(children="1", child_name="Tampered", contact_email="other@example.invalid")
    response = client.post(book_path, data)
    assert response.status_code == 302
    item = VisitBooking.objects.get(admission_case=case)
    assert item.child_name == "Sam" and item.contact_email == case.lead.email
    progress = client.get(release)
    assert progress.context["booking_admissions"]["booking"] == item
    assert b"Playdate booking &amp; attendance" in progress.content
    assert item.slot.session.room.name.encode() in progress.content
    assert client.post(book_path, data).url == response.url
    assert b"Application status" in client.get(response.url).content
    case.submission.refresh_from_db()
    assert case.submission.status == "Playdate Scheduled"
    assert b"Choose a playdate" not in client.get(status_url).content
    detail = client.get(core_url(w, "school_submission_detail", submission_id=case.submission_id))
    assert b"Parent playdate page" in detail.content
    assert b"Admissions progress" in client.get(url(w.tour, "visit_detail", public_id=item.public_id)).content
    assert client.get(url(w.tour, "visit_book", pk=w.slot.pk)).status_code == 404
    assert b"Classroom playdate" not in client.get(url(w.tour, "visit_catalogue")).content
    assert client.post(core_url(w, "school_status_change_request", token=case.submission.status_token)).url == path
    assert client.post(core_url(w, "school_submission_schedule_playdate", submission_id=case.submission_id)).status_code == 302


def test_playdate_catalogue_filters_closed_slots_before_limit(workflow, client):
    w = workflow
    case = eligible_case(w)
    for offset in range(50):
        start = w.slot.session.starts_at + timedelta(minutes=offset)
        session = Session.objects.create(offering=w.slot.program.offering, room=w.slot.session.room,
            capacity=4, starts_at=start, ends_at=start+timedelta(minutes=30), is_published=False)
        VisitSlot.objects.create(program=w.slot.program, session=session, host=w.slot.host)
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=w.slot.session.starts_at+timedelta(days=1),
        ends_at=w.slot.session.ends_at+timedelta(days=1))
    response = client.get(url(w.tour, "playdates", token=case.submission.status_token))
    assert [slot.pk for slot in response.context["slots"]] == [w.slot.pk]


@pytest.mark.parametrize("change", ["offering", "host", "user", "role"])
def test_playdate_catalogue_hides_unavailable_hosts_and_programs(workflow, client, change):
    w = workflow
    case = eligible_case(w)
    if change == "offering":
        Offering.objects.filter(pk=w.slot.program.offering_id).update(is_published=False)
    elif change == "host":
        SchedulingMembership.objects.filter(pk=w.slot.host_id).update(is_active=False)
    elif change == "user":
        w.user.is_active = False
        w.user.save()
    else:
        SchedulingMembership.objects.filter(pk=w.slot.host_id).update(role="viewer")
    assert not client.get(url(w.tour, "playdates", token=case.submission.status_token)).context["slots"]


def test_policy_without_visit_programs_prevents_feature_removal(workflow):
    school = School.objects.create(slug="policy-only", display_name="Policy only")
    profile = SchedulingProfile.objects.create(school=school, capabilities=["visits"])
    AdmissionPolicy.objects.create(profile=profile, enabled=True, require_tour=False)
    assert not VisitProgram.objects.filter(offering__location__profile=profile).exists()
    with pytest.raises(ValidationError, match="saved setup or records"):
        validate_removal(profile, [])


@pytest.mark.parametrize("state,expected", [("attended", "Playdate Completed"), ("canceled", "New"), ("no_show", "New")])
def test_playdate_outcomes_restore_or_advance_only_owned_status(workflow, state, expected):
    w = workflow
    case = eligible_case(w)
    item, _ = reserve(w, case)
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=timezone.now()-timedelta(minutes=10))
    visits.change_state(w.user, w.school.slug, item.public_id, state, "booked")
    case.submission.refresh_from_db()
    assert case.submission.status == expected
    if state == "canceled":
        assert admissions.playdate_reason(case) == ""
        Session.objects.filter(pk=w.slot.session_id).update(starts_at=w.slot.session.starts_at)
        reserve(w, case)
        assert case.playdates.count() == 2


def test_manual_status_and_policy_changes_do_not_rewrite_booked_status_mapping(workflow):
    w = workflow
    case = eligible_case(w)
    item, _ = reserve(w, case)
    w.policy.completed_status = "Archived"
    w.policy.save()
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=timezone.now()-timedelta(minutes=10))
    visits.change_state(w.user, w.school.slug, item.public_id, "attended", "booked")
    case.submission.refresh_from_db()
    assert case.submission.status == "Playdate Completed"
    # A staff decision made after booking takes precedence over cancellation.
    case.playdates.update(state="canceled")
    case.submission.status = "New"
    case.submission.save()
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=w.slot.session.starts_at)
    item, _ = reserve(w, case)
    Submission.objects.filter(pk=case.submission_id).update(status="Enrolled")
    visits.change_state(w.user, w.school.slug, item.public_id, "canceled", "booked")
    case.submission.refresh_from_db()
    assert case.submission.status == "Enrolled"


def test_siblings_with_same_parent_email_have_separate_applications_and_playdates(workflow):
    w = workflow
    first = eligible_case(w)
    # Another attended tour at a different time, for the same parent.
    later = w.tour.session.starts_at + timedelta(days=2)
    session = Session.objects.create(offering=w.tour.program.offering, room=w.tour.session.room, capacity=4,
        starts_at=later, ends_at=later+timedelta(minutes=30), is_published=True)
    w.tour = VisitSlot.objects.create(program=w.tour.program, session=session, host=w.tour.host)
    second = eligible_case(w)
    assert first.lead_id != second.lead_id and first.submission_id != second.submission_id
    reserve(w, first)
    reserve(w, second)
    assert VisitBooking.objects.filter(admission_case__isnull=False).count() == 2
    assert visits.places_left(w.slot) == 0


@pytest.mark.parametrize("change", ["status", "approval", "policy", "profile", "family-portal", "lost", "wrong-kind", "wrong-token-case"])
def test_stale_booking_and_identity_guards(workflow, change):
    w = workflow
    case = eligible_case(w)
    data = playdate_data(w, case)
    error = ValidationError
    if change == "status":
        Submission.objects.filter(pk=case.submission_id).update(status="Archived")
    elif change == "approval":
        AdmissionCase.objects.filter(pk=case.pk).update(playdate_released_at=None)
    elif change == "policy":
        AdmissionPolicy.objects.filter(pk=w.policy.pk).update(enabled=False)
    elif change == "profile":
        SchedulingProfile.objects.filter(pk=w.profile.pk).update(enabled=False)
        error = Http404
    elif change == "family-portal":
        w.school.feature_flags["family_portal_enabled"] = False
        w.school.save()
    elif change == "lost":
        # Application guard, independent of scheduling permissions.
        case.lead.status = "lost"
        case.lead.converted_submission_id = None
        case.submission_id = None
        assert "contact the school" in admissions.application_reason(case)
        return
    elif change == "wrong-kind":
        w.slot.program.kind = "tour"
        w.slot.program.save()
        error = Http404
    else:
        data["booking_token"] = visits.booking_token(w.slot, "browser-a")
    with pytest.raises(error):
        visits.book(w.school.slug, w.slot.pk, data, "browser-a", case=case)
    assert not VisitBooking.objects.filter(admission_case=case).exists()


def test_policy_forms_validate_scoped_statuses_stale_writes_and_history(workflow, client):
    w = workflow
    client.force_login(w.user)
    path = url(w.tour, "admission_policy")
    form = client.get(path).context["form"]
    values = post_data(form)
    assert client.post(path, {**values, "eligible_statuses": ["Nonexistent"]}).status_code == 400
    assert client.post(path, {**values, "completed_status": "Playdate Scheduled"}).status_code == 400
    assert client.post(path, {**values, "eligible_statuses": ["Playdate Completed"]}).status_code == 400
    assert client.post(path, values).status_code == 302
    assert client.post(path, {**values, "require_tour": False}).status_code == 302
    assert client.post(path, values).status_code == 400
    case = completed_tour(w)
    fresh = post_data(client.get(path).context["form"], enabled=False)
    assert client.post(path, fresh).status_code == 400
    assert AdmissionCase.objects.filter(pk=case.pk).exists()
    w.school.feature_flags["family_portal_enabled"] = False
    w.school.save()
    assert client.post(path, post_data(client.get(path).context["form"])).status_code == 400


def test_new_policy_requires_both_owner_roles_and_signed_creation(workflow, client):
    w = workflow
    w.policy.delete()
    client.force_login(w.user)
    path = url(w.tour, "admission_policy")
    values = post_data(client.get(path).context["form"], enabled=True, eligible_statuses=["New"])
    assert client.post(path, {**values, "create_revision": "bad"}).status_code == 400
    assert client.post(path, values).status_code == 302
    SchoolAdminMembership.objects.filter(user=w.user).update(role="editor")
    assert client.get(path).status_code == 404
    SchoolAdminMembership.objects.filter(user=w.user).delete()
    assert client.get(path).status_code == 404


def test_optional_tour_and_legacy_converted_lead_are_not_silently_imported(workflow, client):
    w = workflow
    w.policy.require_tour = False
    w.policy.save()
    lead = Lead.objects.create(school=w.school, name="Another parent", email="another@example.invalid")
    case = admissions.case_for_lead(w.policy, lead, create=True)
    assert not case.tour_id and not admissions.application_reason(case)
    first = admissions.prepare_draft(case)
    case.refresh_from_db()
    assert admissions.prepare_draft(case).pk == first.pk
    legacy = Lead.objects.create(school=w.school, name="Legacy", email="legacy@example.invalid",
        converted_submission=Submission.objects.create(school=w.school))
    with pytest.raises(ValidationError, match="not connected"):
        admissions.case_for_lead(w.policy, legacy, create=True)
    draft = DraftSubmission.objects.create(school=w.school, lead=legacy)
    assert client.get(core_url(w, "apply_resume", token=draft.token)).status_code == 403
    client.force_login(w.user)
    assert client.get(url(w.tour, "admission_case", lead_pk=legacy.pk)).status_code == 302


@pytest.mark.parametrize("problem", ["expired", "submitted", "different-draft", "foreign-draft"])
def test_invalid_drafts_cannot_open_application(workflow, client, problem):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    if problem == "expired":
        draft.token_expires_at = timezone.now()-timedelta(minutes=1)
    elif problem == "submitted":
        draft.submitted_at = timezone.now()
    elif problem == "different-draft":
        draft.submitted_at = timezone.now()
        draft.save()
        draft = DraftSubmission.objects.create(school=w.school, lead=case.lead)
    else:
        draft.school = School.objects.create(slug="different", display_name="Different")
    draft.save()
    assert client.get(core_url(w, "apply_resume", token=draft.token)).status_code == 403


def test_release_controls_fail_closed_and_manual_creation_is_gated(workflow, client):
    w = workflow
    item = book(w.tour)
    client.force_login(w.user)
    path = url(w.tour, "admission_case", lead_pk=item.lead_id)
    assert b"Record tour attendance" in client.post(path, {"action": "release_application", "revision": ""}, follow=True).content
    assert b"must be submitted" in client.post(path, {"action": "release_playdate", "revision": ""}, follow=True).content
    assert client.get(core_url(w, "school_submission_create")).status_code == 302
    SchoolAdminMembership.objects.filter(user=w.user).update(role="viewer")
    assert client.get(path).status_code == 200
    assert client.post(path, {"action": "release_application", "revision": ""}).status_code == 404
    SchoolAdminMembership.objects.filter(user=w.user).delete()
    assert client.get(path).status_code == 404


def test_cross_tenant_status_tokens_and_receipts_never_authorize(workflow, client):
    w = workflow
    case = submitted_case(w)
    other = School.objects.create(slug="other-preschool", display_name="Other")
    SchedulingProfile.objects.create(school=other, enabled=True, capabilities=["visits"])
    assert client.get(reverse("scheduling:playdates", kwargs={"school_slug": other.slug,
        "token": case.submission.status_token})).status_code == 404
    assert client.get(url(w.tour, "playdates", token="made-up")).status_code == 404
    w.school.feature_flags["family_portal_enabled"] = False
    w.school.save()
    assert client.get(url(w.tour, "playdates", token=case.submission.status_token)).status_code == 404
    foreign_lead = Lead.objects.create(school=other, name="Foreign")
    with pytest.raises(ValidationError, match="another school"):
        admissions.case_for_lead(w.policy, foreign_lead)


def test_linking_errors_rollback_new_submission_and_leave_draft_open(workflow, monkeypatch):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    client = Client()
    client.get(core_url(w, "apply_resume", token=draft.token), follow=True)
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(admissions, "_audit", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(core_url(w, "apply"), {"student_name": "Sam", "guardian_name": "Taylor", "contact_email": case.lead.email})
    case.refresh_from_db()
    draft.refresh_from_db()
    case.lead.refresh_from_db()
    assert not case.submission_id and not case.lead.converted_submission_id and not draft.submitted_at
    assert not Submission.objects.exists()


def test_disabled_or_absent_policy_is_a_true_legacy_pass_through(workflow):
    w = workflow
    request = RequestFactory().get("/")
    view = lambda *args, **kwargs: HttpResponse("legacy")
    for disabled in [True, False]:
        if disabled:
            w.policy.enabled = False
            w.policy.save()
        else:
            w.policy.delete()
        assert application_gate(view)(request, w.school.slug).content == b"legacy"
        assert lead_application_gate(view)(request, w.school.slug, 999).content == b"legacy"
        assert admissions.lead_context(Lead(school=w.school)) is None
        assert admissions.family_context(Submission.objects.create(school=w.school)) is None
    admissions.record_submission(request, None, None)


@pytest.mark.parametrize("data,name", [({"student_name": "Sam"}, "Sam"),
    ({"student_name": "Fallback", "student_first_name": "First", "student_last_name": "Last"}, "First Last"),
    ({"student_name": "Fallback", "applicant_name": "Applicant"}, "Applicant"),
    ({"child_first_name": "Kid", "child_last_name": "Works"}, "Kid Works")])
def test_student_name_fallback_preserves_existing_name_precedence(workflow, data, name):
    submission = Submission.objects.create(school=workflow.school, data=data)
    assert submission.student_display_name() == name
    assert name.lower() in submission.search_text.lower()


@pytest.mark.django_db(transaction=True)
def test_concurrent_playdate_submissions_claim_exactly_one_booking(workflow):
    w = workflow
    case = eligible_case(w)
    data = [playdate_data(w, case), playdate_data(w, case)]
    barrier = Barrier(2)
    def attempt(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                item, _ = visits.book(w.school.slug, w.slot.pk, data[index], "browser-a", case=case)
                return item.pk
            except ValidationError:
                return None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    assert case.playdates.count() == 1
    assert visits.places_left(w.slot) == 2


def test_multi_form_completion_links_the_exact_case(workflow, client):
    from core.services.config_loader import load_school_config
    from core.tests.test_apply_flow import _build_valid_post_data
    w = workflow
    w.school.slug = "multi-form-demo"
    w.school.save()
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    client.get(core_url(w, "apply_resume", token=draft.token))
    config = load_school_config(w.school.slug)
    for key, value in config.raw["forms"].items():
        response = client.post(core_url(w, "apply_form", form_key=key), _build_valid_post_data(value["form"]))
        assert response.status_code == 302
    case.refresh_from_db()
    draft.refresh_from_db()
    assert case.submission_id and draft.submitted_at
    assert response.url == core_url(w, "family_status", token=case.submission.status_token)


def test_payment_entry_is_denied_before_provider_call_and_links_after_verification(workflow, client, monkeypatch):
    from unittest.mock import Mock
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    draft.data = {"student_name": "Sam", "guardian_name": "Taylor", "contact_email": case.lead.email}
    draft.save()
    retrieve = Mock(return_value=SimpleNamespace(status="succeeded"))
    monkeypatch.setattr("core.views_public.retrieve_application_fee_intent", retrieve)
    path = core_url(w, "apply_payment_confirm", draft_token=draft.token)
    w.policy.application_release = "staff"
    w.policy.save()
    assert client.get(path, {"payment_intent": "pi_local_example"}).status_code == 403
    retrieve.assert_not_called()
    case.application_released_at = timezone.now()
    case.save()
    response = client.get(path, {"payment_intent": "pi_local_example"})
    case.refresh_from_db()
    assert response.url == core_url(w, "family_status", token=case.submission.status_token)
    assert case.submission.payment_status == "paid"
    assert client.get(path, {"payment_intent": "pi_local_example"}).url == response.url
    retrieve.assert_called_once()
    draft.token_expires_at = timezone.now()-timedelta(seconds=1)
    draft.save()
    assert client.get(core_url(w, "apply_resume", token=draft.token)).status_code == 403


def test_link_service_rejects_mismatched_identity_and_already_converted_lead(workflow):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    case.refresh_from_db()
    request = SimpleNamespace(booking_admission=case)
    sub = Submission.objects.create(school=w.school)
    with pytest.raises(ValidationError, match="identity changed"):
        admissions.record_submission(request, None, sub)
    case.policy.enabled = False
    with pytest.raises(ValidationError, match="paused"):
        admissions.record_submission(request, draft, sub)
    case.policy.enabled = True
    Lead.objects.filter(pk=case.lead_id).update(converted_submission=sub)
    with pytest.raises(ValidationError, match="already exists"), transaction.atomic():
        admissions.record_submission(request, draft, sub)
    case.refresh_from_db()
    assert not case.submission_id


def test_staff_opening_application_preserves_family_edits(workflow, client):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    draft.data = {"student_name": "Family correction", "guardian_name": "Updated guardian", "contact_email": "updated@example.invalid", "notes": "Keep me"}
    draft.save()
    client.force_login(w.user)
    path = core_url(w, "school_lead_detail", lead_id=case.lead_id)
    assert client.get(path).status_code == 200
    start = core_url(w, "school_lead_start_enrollment", lead_id=case.lead_id)
    assert client.post(start).status_code == 302
    assert client.post(start, {"action": "copy"}).url == path
    saved = DraftSubmission.objects.get(pk=draft.pk)
    assert all(saved.data[key] == value for key, value in draft.data.items())
    assert saved.data["tour_slot"]
    w.policy.enabled = False
    w.policy.save()
    with pytest.raises(ValidationError, match="settings changed"):
        admissions.prepare_lead_draft(case.lead)


def test_playdate_configuration_and_failed_submission_views(workflow, client):
    w = workflow
    form = visits.program_form(w.profile, pk=w.slot.program_id)
    invalid = visits.save_program(w.user, w.school.slug, post_data(form, capture_leads=True), w.slot.program_id)
    assert "capture_leads" in invalid.errors
    case = eligible_case(w)
    path = url(w.tour, "playdate_book", token=case.submission.status_token, pk=w.slot.pk)
    page = client.get(path)
    w.policy.eligible_statuses = ["In Review"]
    w.policy.save()
    response = client.post(path, playdate_data(w, case, booking_token=page.context["booking_token"]))
    assert response.status_code == 400 and b"reviewing" in response.content
    w.policy.eligible_statuses = ["New"]
    w.policy.playdate_release = "automatic"
    w.policy.save()
    w.slot.program.offering.is_published = False
    w.slot.program.offering.save()
    assert b"Classroom playdate" not in client.get(url(w.tour, "playdates", token=case.submission.status_token)).content
    assert client.get(path).status_code == 403
    csrf_client = Client(enforce_csrf_checks=True)
    assert csrf_client.post(path, {}).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_simultaneous_application_submissions_create_one_linked_record(workflow):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    clients = [Client(), Client()]
    for client in clients:
        client.get(core_url(w, "apply_resume", token=draft.token), follow=True)
    barrier = Barrier(2)
    def submit(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return clients[index].post(core_url(w, "apply"), {"student_name": "Sam", "guardian_name": "Taylor",
                "contact_email": case.lead.email, "_draft_token": draft.token}).url
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    case.refresh_from_db()
    assert responses == [core_url(w, "family_status", token=case.submission.status_token)] * 2
    assert Submission.objects.count() == 1
