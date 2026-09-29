"""Migration-free admissions display and inert fee preview, isolated from SBMC."""
import csv
import io
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Lead, LEAD_STATUS_CHOICES
from core.services.admissions_workflow import with_admissions_status
from core.services.admin_lead_yaml import get_lead_status_choices, get_lead_workflow_filters
from core.services.config_loader import load_school_config
from core.services.lead_conversion import try_convert_lead
from core.services.payment_preview import get_payment_preview
from core.tests.factories import LeadFactory, SchoolFactory, SchoolAdminMembershipFactory, SubmissionFactory

pytestmark = pytest.mark.django_db
SLUG = "kidworks-childrens-center"


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def school(client):
    school = SchoolFactory(slug=SLUG, plan="pro")
    membership = SchoolAdminMembershipFactory(school=school, role="owner")
    client.force_login(membership.user)
    return school


def url(name, school, **kwargs):
    return reverse(name, kwargs={"school_slug": school.slug, **kwargs})


def linked_lead(school, status="New", **kwargs):
    submission = SubmissionFactory(school=school, status=status)
    return LeadFactory(school=school, converted_submission=submission, status="enrolled", **kwargs)


@pytest.mark.parametrize("status,expected", [
    ("New", "application_submitted"), ("In Review", "application_submitted"),
    ("Playdate Scheduled", "application_submitted"), ("Playdate Completed", "application_submitted"),
    ("Fee Pending", "application_submitted"), ("Waitlisted", "application_submitted"),
    ("Declined", "application_submitted"), ("Archived", "application_submitted"), ("Enrolled", "enrolled"),
])
def test_list_detail_and_csv_agree_without_writing(client, school, status, expected):
    lead = linked_lead(school, status)
    original = lead.updated_at
    response = client.get(url("school_leads", school), {"status": expected})
    assert response.status_code == 200
    row = response.context["leads"][0]
    assert row["status_raw"] == expected
    label = "Application Submitted" if expected == "application_submitted" else "Enrolled"
    assert row["status_override"] == label
    assert row["admissions_managed"] is True
    assert not any(tag == "input" and attrs.get("class") == "lead-checkbox" for tag, attrs in Elements(response.content.decode()).tags)
    assert response.context["leads_metrics"][expected] == 1
    assert response.context["leads_metrics"]["enrolled"] == int(expected == "enrolled")
    detail = client.get(url("school_lead_detail", school, lead_id=lead.pk))
    assert detail.status_code == 200
    assert detail.context["lead_status_value"] == expected
    assert detail.context["lead_status_label"] == label
    assert b"This lead has been enrolled" not in detail.content
    html = detail.content.decode()
    assert f'class="sub-pipeline__step sub-pipeline__step--active">{label}</span>' in html
    assert not any(tag == "button" and attrs.get("value") == "application_submitted" for tag, attrs in Elements(html).tags)
    assert not any(tag == "button" and attrs.get("name") == "new_status" for tag, attrs in Elements(html).tags)
    export = client.get(url("school_lead_export", school), {"status": expected})
    rows = list(csv.DictReader(io.StringIO(export.content.decode())))
    assert len(rows) == 1 and rows[0]["Status"] == expected
    lead.refresh_from_db()
    assert lead.status == "enrolled" and lead.updated_at == original


def test_existing_records_filters_metrics_and_enrollment_reversal(client, school):
    pending = linked_lead(school, "Fee Pending")
    enrolled = linked_lead(school, "Enrolled")
    LeadFactory(school=school, status="new")
    response = client.get(url("school_leads", school), {"filter": "applications"})
    assert [row["id"] for row in response.context["leads"]] == [pending.pk]
    assert response.context["leads_metrics"] == {"new": 1, "contacted": 0, "enrolled": 1, "application_submitted": 1}
    enrolled.converted_submission.status = "In Review"
    enrolled.converted_submission.save(update_fields=["status"])
    response = client.get(url("school_leads", school), {"filter": "won"})
    assert not response.context["leads"]
    assert response.context["leads_metrics"]["application_submitted"] == 2


@pytest.mark.parametrize("filter_name", ["needs_follow_up", "stale", "not_converted"])
def test_handoff_is_not_pending_lead_followup(client, school, filter_name):
    lead = linked_lead(school, "Fee Pending", next_follow_up_at=timezone.now() - timedelta(days=7))
    Lead.objects.filter(pk=lead.pk).update(status="contacted", updated_at=timezone.now() - timedelta(days=7))
    response = client.get(url("school_leads", school), {"filter": filter_name})
    assert not response.context["leads"]


def test_lost_and_unlinked_statuses_are_preserved(client, school):
    lead = linked_lead(school)
    Lead.objects.filter(pk=lead.pk).update(status="lost")
    unlinked = LeadFactory(school=school, status="trial_completed")
    response = client.get(url("school_leads", school))
    rows = {row["id"]: row["status_raw"] for row in response.context["leads"]}
    assert rows == {lead.pk: "lost", unlinked.pk: "trial_completed"}


def test_tenant_isolation_and_no_query_per_row(client, school, django_assert_num_queries):
    outsider = linked_lead(SchoolFactory(), "Enrolled")
    linked_lead(school)
    raw = load_school_config(SLUG).raw
    with django_assert_num_queries(1):
        statuses = list(with_admissions_status(Lead.objects.filter(school=school), raw).values_list("admissions_status", flat=True))
    assert statuses == ["application_submitted"]
    assert client.get(url("school_lead_detail", school, lead_id=outsider.pk)).status_code == 404
    assert str(outsider.pk) not in [r["Lead ID"] for r in csv.DictReader(io.StringIO(client.get(url("school_lead_export", school)).content.decode()))]


def test_derived_state_is_not_a_manual_write_target(client, school):
    lead = LeadFactory(school=school, status="new")
    response = client.post(url("school_lead_status_update", school, lead_id=lead.pk), {"new_status": "application_submitted"})
    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "new"
    assert "application_submitted" not in dict(LEAD_STATUS_CHOICES)


def test_sbmc_behavior_and_query_unchanged(client):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    client.force_login(SchoolAdminMembershipFactory(school=school, role="owner").user)
    lead = linked_lead(school, "New")
    raw = load_school_config(school.slug).raw
    qs = Lead.objects.filter(school=school)
    assert with_admissions_status(qs, raw) is qs
    assert get_lead_status_choices(raw) == list(LEAD_STATUS_CHOICES)
    response = client.get(url("school_leads", school), {"status": "enrolled"})
    assert [row["id"] for row in response.context["leads"]] == [lead.pk]
    assert response.context["leads_metrics"] == {"new": 0, "contacted": 0, "enrolled": 1}
    detail = client.get(url("school_lead_detail", school, lead_id=lead.pk))
    assert detail.context["lead_status_value"] == "enrolled"
    assert b"Application Submitted" not in detail.content
    assert not get_lead_workflow_filters({"admin": {"lead_workflow": {"filters": {"applications": {"label": "Applications", "statuses": ["application_submitted"]}}}}})


def test_existing_conversion_service_hands_off_then_reflects_enrollment(client, school):
    lead = LeadFactory(school=school, status="trial_completed")
    sub = SubmissionFactory(school=school, status="New")
    raw = load_school_config(school.slug).raw
    assert try_convert_lead(school=school, submission=sub, config_raw=raw, lead=lead).pk == lead.pk
    assert client.get(url("school_lead_detail", school, lead_id=lead.pk)).context["lead_status_value"] == "application_submitted"
    sub.status = "Enrolled"
    sub.save(update_fields=["status"])
    assert client.get(url("school_lead_detail", school, lead_id=lead.pk)).context["lead_status_value"] == "enrolled"


def test_foreign_application_does_not_influence_display(school):
    foreign_sub = SubmissionFactory(status="Enrolled")
    lead = LeadFactory(school=school, status="contacted", converted_submission=foreign_sub)
    assert with_admissions_status(Lead.objects.all(), load_school_config(school.slug).raw).get(pk=lead.pk).admissions_status == "contacted"


@pytest.mark.parametrize("status,visible", [("New", False), ("Playdate Completed", False), ("Fee Pending", True), ("Enrolled", False)])
def test_payment_preview_is_inert_and_status_scoped(client, school, status, visible):
    sub = SubmissionFactory(school=school, status=status)
    original = sub.updated_at
    response = client.get(url("family_status", school, token=sub.status_token))
    assert response.status_code == 200
    html = response.content.decode()
    assert ('id="payment-preview"' in html) == visible
    if visible:
        panel = html.split('id="payment-preview">', 1)[1].split('<!-- Scheduling preferences -->', 1)[0]
        tags = Elements(panel).tags
        assert "$50.00" in panel and "Demo only" in panel
        assert '<div class="card__title">Enrollment fee</div>' in panel
        assert '<div class="meta-item">Amount: <strong>$50.00</strong></div>' in panel
        assert not any(tag in ("h1", "h2", "h3") for tag, attrs in tags)
        assert any(tag == "button" and "btn-submit" in attrs.get("class", "").split() for tag, attrs in tags)
        assert any(tag == "button" and attrs.get("type") == "button" and "disabled" in attrs for tag, attrs in tags)
        assert not any(tag in ("form", "a", "script") for tag, attrs in tags)
        assert "Online payments are not enabled" in panel
    sub.refresh_from_db()
    assert sub.status == status and sub.updated_at == original


def test_sbmc_parent_page_has_no_fee_preview(client):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    sub = SubmissionFactory(school=school, status="Fee Pending")
    response = client.get(url("family_status", school, token=sub.status_token))
    assert response.status_code == 200
    assert b'id="payment-preview"' not in response.content
    assert b"Lesson Scheduling Preferences" in response.content


@pytest.mark.parametrize("amount", [None, "", "abc", "NaN", "Infinity", "-1", "0", "50.001", "1000000"])
def test_invalid_preview_amount_is_not_displayed(amount):
    assert get_payment_preview({"family_portal": {"payment_preview": {"enabled": True, "amount": amount}}}, "Fee Pending") is None


@pytest.mark.parametrize("raw", [{}, {"family_portal": None}, {"family_portal": {"payment_preview": None}}, {"family_portal": {"payment_preview": {"enabled": "true", "amount": 50}}}])
def test_preview_requires_explicit_opt_in(raw):
    assert get_payment_preview(raw, "Fee Pending") is None
