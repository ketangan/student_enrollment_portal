"""Presentation choices must not silently change a school's business rules."""
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from scheduling import school_navigation
from scheduling.school_navigation import OPTIONAL_FEATURES, school_policy, workflow_policy
from scheduling.tests.test_lessons import music, book
from scheduling.tests.test_appointments import offer, booked


@pytest.fixture
def configure(monkeypatch):
    def apply(navigation=None, workflows=None, *, legacy=False):
        raw = {"navigation": navigation or {}}
        if not legacy:
            raw["workflows"] = {} if workflows is None else workflows
        monkeypatch.setattr(school_navigation, "load_school_config",
            lambda slug: SimpleNamespace(raw={"scheduling": raw}))
        return raw
    return apply


def policy():
    return school_policy(SimpleNamespace(slug="any-school"))


@pytest.mark.parametrize("navigation", [{}, {"layout": "school"},
    {"layout": "school", "simplified": True, "single_location": True}])
def test_explicit_workflow_defaults_are_independent_of_layout(configure, navigation):
    configure(navigation)
    assert policy()["workflows"] == {
        "teacher_self_service": False, "direct_enrollment": False, "enrollment_linking": False,
        "reschedule_reason_required": True,
        "optional_features": list(OPTIONAL_FEATURES), "lesson_changes": "series", "class_setup": "checkout"}
    assert not set(policy()["workflows"]).intersection(policy()["navigation"])


@pytest.mark.parametrize("simplified", [False, True])
def test_legacy_settings_normalize_to_equivalent_explicit_settings(configure, simplified):
    nav = {"layout": "school", "simplified": simplified, "features": ["lessons", "workshops"],
        "teacher_self_service": True, "direct_enrollment": True,
        "enrollment_linking": True, "optional_features": []}
    configure(nav, legacy=True)
    before = policy()
    configure(before["navigation"], before["workflows"])
    assert policy() == before


@pytest.mark.parametrize("value", [None, [], True, "yes"])
def test_invalid_workflow_section_fails_closed(monkeypatch, value):
    monkeypatch.setattr(school_navigation, "load_school_config",
        lambda slug: SimpleNamespace(raw={"scheduling": {"workflows": value}}))
    with pytest.raises(ValidationError, match="mapping"):
        policy()


@pytest.mark.parametrize("key", school_navigation.WORKFLOW_FLAGS)
@pytest.mark.parametrize("value", [None, 1, "false", [], {}])
def test_new_workflow_booleans_are_strict(configure, key, value):
    configure(workflows={key: value})
    with pytest.raises(ValidationError, match="true or false"):
        policy()


@pytest.mark.parametrize("workflow", [{"lesson_changes": "all"}, {"class_setup": None},
    {"optional_features": "inventory"}, {"optional_features": ["unknown"]}, {"typo": True}])
def test_unknown_or_invalid_workflow_options_rejected(configure, workflow):
    configure(workflows=workflow)
    with pytest.raises(ValidationError):
        policy()


@pytest.mark.parametrize("value", [None, "false", 0, []])
def test_reschedule_note_policy_requires_boolean(configure, value):
    configure(workflows={"reschedule_reason_required": value})
    with pytest.raises(ValidationError, match="reschedule_reason_required"):
        policy()


@pytest.mark.parametrize("key", [*school_navigation.WORKFLOW_FLAGS, "optional_features"])
def test_two_configuration_sources_are_rejected_even_when_values_agree(configure, key):
    value = [] if key == "optional_features" else True
    configure({"layout": "school", key: value}, {key: value})
    with pytest.raises(ValidationError, match="Move all workflow"):
        policy()


def test_workflow_setting_in_wrong_section_is_not_silently_ignored(configure):
    configure({"lesson_changes": "single"})
    with pytest.raises(ValidationError, match="navigation"):
        policy()


@pytest.mark.parametrize("slug", ["south-bay-music", "plushumi-demo", "another-school"])
def test_opted_school_cannot_change_another_schools_policy(monkeypatch, slug):
    original = school_navigation.load_school_config
    baseline = school_policy(SimpleNamespace(slug=slug))
    monkeypatch.setattr(school_navigation, "load_school_config", lambda name:
        SimpleNamespace(raw={"scheduling": {"workflows": {"direct_enrollment": True}}})
        if name == "opted-school" else original(name))
    assert workflow_policy(SimpleNamespace(slug="opted-school"))["direct_enrollment"]
    assert school_policy(SimpleNamespace(slug=slug)) == baseline


@pytest.mark.django_db
@pytest.mark.parametrize("simplified", [False, True])
@pytest.mark.parametrize("scope", ["single", "series"])
def test_lesson_rules_follow_workflow_not_visual_preset(music, configure, simplified, scope):
    from scheduling.lesson_change_forms import RescheduleForm, validate_change_scope
    from scheduling.lesson_forms import CancelLessonForm
    configure({"layout": "school", "simplified": simplified}, {"lesson_changes": scope})
    series = book(music)
    occurrence = series.occurrences.first()
    for form in (RescheduleForm(occurrence=occurrence), CancelLessonForm(occurrence=occurrence)):
        assert form.fields["scope"].widget.is_hidden is (scope == "single")
    if scope == "single":
        with pytest.raises(ValidationError):
            validate_change_scope(music["profile"], "future")
    else:
        validate_change_scope(music["profile"], "future")
    assert series.occurrences.count() == 3


@pytest.mark.django_db
@pytest.mark.parametrize("layout,simplified", [("bookings", False), ("school", False), ("school", True)])
@pytest.mark.parametrize("mode", ["checkout", "requests"])
def test_class_setup_uses_workflow_without_rewriting_saved_prices(music, configure, layout, simplified, mode):
    from decimal import Decimal
    from scheduling.forms import OfferingForm
    from scheduling.models import Offering
    configure({"layout": layout, "simplified": simplified}, {"class_setup": mode, "optional_features": []})
    item = Offering.objects.create(location=music["location"], name="Existing class",
        price_per_participant=Decimal("40"), deposit_per_participant=Decimal("10"))
    form = OfferingForm(profile=music["profile"], instance=item)
    assert ("accepts_requests" in form.fields) is (mode == "requests")
    assert ("payment_at_booking" in form.fields) is (mode == "checkout")
    assert not {"inventory_catalog", "allow_deferred_selections"}.intersection(form.fields)
    form = OfferingForm({"name": "Updated class", "location": item.location_id,
        "price_per_participant": "40", "accepts_requests": "on",
        "payment_at_booking": "deposit", "deposit_per_participant": "10" if mode == "checkout" else "0",
        "allow_deferred_selections": "on"}, profile=music["profile"], instance=item)
    assert form.is_valid(), form.errors
    form.save()
    item.refresh_from_db()
    assert item.price_per_participant == 40 and item.deposit_per_participant == 10
    assert item.accepts_requests is (mode == "requests")
    assert not item.allow_deferred_selections


@pytest.mark.django_db
def test_teacher_workflow_does_not_grant_capability_or_account_binding(music, configure):
    from django.http import Http404
    from scheduling.teacher_availability import teacher_access
    configure(workflows={"teacher_self_service": True})
    with pytest.raises(Http404):
        teacher_access(music["user"], music["slug"])
    music["teacher"].login_user = music["user"]
    music["teacher"].save(update_fields=["login_user"])
    music["profile"].capabilities = []
    music["profile"].save(update_fields=["capabilities"])
    with pytest.raises(Http404):
        teacher_access(music["user"], music["slug"])


@pytest.mark.django_db
@pytest.mark.parametrize("simplified", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_preview_payment_control_and_post_use_same_workflow_gate(music, offer, configure, client, simplified, enabled):
    from scheduling.tests.test_lessons import url
    configure({"layout": "school", "simplified": simplified},
        {"optional_features": ["operator_tools"] if enabled else []})
    offer.fee_cents = 3000
    offer.save()
    booking, _, _ = booked(music, offer)
    client.force_login(music["user"])
    endpoint = url(music, "lesson_booking_detail", public_id=booking.public_id)
    response = client.get(endpoint)
    assert response.status_code == 200
    assert (b'Record test payment / refund' in response.content) is enabled
    response = client.post(endpoint, {})
    assert response.status_code == (400 if enabled else 404)
    assert not booking.order.entries.exists()
