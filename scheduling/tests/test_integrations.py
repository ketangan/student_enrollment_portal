from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
import json
import uuid

from cryptography.fernet import Fernet
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, IntegrityError, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse

from core.models import AdminAuditLog, School
from scheduling.capabilities import validate_removal
from scheduling.integrations import service, vault
from scheduling.integrations.payloads import (
    DESTINATIONS, MAPPING_FIELDS, NotionDestination, Projection, example_booking, rich_text, validate_mapping,
)
from scheduling.integrations.registry import PROVIDERS, Provider, provider
from scheduling.models import IntegrationConnection, SchedulingProfile
from scheduling.features import feature_form, save_features

pytestmark = pytest.mark.django_db
ID = "b55c9c91-384d-452b-81db-d1ef79372b75"
SECRET = "test-only-private-credential-do-not-expose"
MAPPING = {key: label for key, (label, _, _) in MAPPING_FIELDS.items()}


@pytest.fixture
def actor(booking_member, settings):
    settings.BOOKINGS_INTEGRATION_KEYS = [Fernet.generate_key()]
    return booking_member


def url(actor, name, **kwargs):
    return reverse(f"scheduling:{name}", kwargs={"school_slug": actor.profile.school.slug, **kwargs})


def data(kind="notion", **extra):
    result = dict(label=f"{kind.title()} preparation", environment="test", credential=SECRET)
    if kind == "wix":
        result.update(site_id=ID, visitor_client_id=ID)
    else:
        result.update(data_source_id=ID, status_type="rich_text", **{f"map_{key}": value for key, value in MAPPING.items()})
    return {**result, **extra}


def save(actor, kind="notion", **extra):
    form = service.save_connection(actor.user, actor.profile.school.slug, data(kind, **extra), provider_key=kind)
    assert form.is_valid(), form.errors
    return form.instance


def edit_data(actor, connection, **extra):
    form = service.form_for(actor.profile, pk=connection.pk)
    return data(connection.provider, credential="", edit_revision=form["edit_revision"].value(), **extra)


def test_native_and_wix_destinations_are_independent(actor, client):
    native = save(actor)
    source = save(actor, "wix")
    remote = save(actor, label="External workshop prep", source=source.pk)
    second = save(actor, label="Second destination")
    assert native.source_id is None and second.source_id is None
    assert remote.source == source and str(source) == "Wix preparation"
    assert source.provider_spec.role == "source" and native.provider_spec.role == "destination"
    actor.profile.refresh_from_db()
    assert not actor.profile.native_reservations_enabled
    assert "enabled" not in {f.name for f in native._meta.fields}
    client.force_login(actor.user)
    response = client.get(url(actor, "integrations"))
    assert response.status_code == 200 and response.content.count(b"Not connected") == 4
    assert client.get(url(actor, "integration_preview", pk=source.pk)).status_code == 404


def test_credentials_encrypted_hidden_audited_and_tenant_bound(actor, client):
    saved = save(actor)
    saved.refresh_from_db()
    assert SECRET not in saved.credential_ciphertext and saved.credential_saved
    assert vault.unseal(saved) == SECRET
    client.force_login(actor.user)
    for name, kwargs in [("integrations", {}), ("integration_edit", {"pk": saved.pk}), ("integration_preview", {"pk": saved.pk})]:
        response = client.get(url(actor, name, **kwargs))
        assert response.status_code == 200
        assert SECRET.encode() not in response.content and saved.credential_ciphertext.encode() not in response.content
        assert response.headers["Cache-Control"].find("no-store") >= 0
    logs = list(AdminAuditLog.objects.values("changes", "extra"))
    assert SECRET not in str(logs) and saved.credential_ciphertext not in str(logs)
    assert {"credential": "replaced"} in [log["changes"] for log in logs]
    for field, value in [("profile_id", saved.profile_id + 99), ("id", uuid.uuid4()), ("provider", "wix"), ("environment", "live")]:
        original = getattr(saved, field)
        setattr(saved, field, value)
        with pytest.raises(ValidationError):
            vault.unseal(saved)
        setattr(saved, field, original)


def test_credentials_keep_replace_remove_and_key_rotation(actor, settings):
    saved = save(actor)
    original = saved.credential_ciphertext
    form = service.save_connection(actor.user, actor.profile.school.slug, edit_data(actor, saved, label="Renamed"), pk=saved.pk)
    assert form.is_valid(), form.errors
    saved.refresh_from_db()
    assert saved.credential_ciphertext == original
    old = settings.BOOKINGS_INTEGRATION_KEYS[0]
    new = Fernet.generate_key()
    settings.BOOKINGS_INTEGRATION_KEYS = [new, old]
    assert vault.unseal(saved) == SECRET
    change = edit_data(actor, saved)
    change["credential"] = "replacement-test-token"
    assert service.save_connection(actor.user, actor.profile.school.slug, change, pk=saved.pk).is_valid()
    saved.refresh_from_db()
    settings.BOOKINGS_INTEGRATION_KEYS = [new]
    assert vault.unseal(saved) == "replacement-test-token"
    assert service.save_connection(actor.user, actor.profile.school.slug, edit_data(actor, saved, clear_credential="on"), pk=saved.pk).is_valid()
    saved.refresh_from_db()
    assert not saved.credential_saved


@pytest.mark.parametrize("keys", [[], "bad", ["invalid"], [None]])
def test_vault_fails_closed_without_valid_platform_keys(actor, settings, keys):
    settings.BOOKINGS_INTEGRATION_KEYS = keys
    form = service.save_connection(actor.user, actor.profile.school.slug, data(), provider_key="notion")
    assert "credential" in form.errors and not IntegrationConnection.objects.exists()
    assert not AdminAuditLog.objects.exists()
    assert save(actor, credential="").credential_ciphertext == ""


def test_vault_environment_keys_and_tampering(actor, settings, monkeypatch):
    saved = save(actor)
    monkeypatch.setenv("BOOKINGS_INTEGRATION_KEYS", settings.BOOKINGS_INTEGRATION_KEYS[0].decode())
    del settings.BOOKINGS_INTEGRATION_KEYS
    assert vault.unseal(saved) == SECRET
    saved.credential_ciphertext = "tampered"
    with pytest.raises(ValidationError):
        vault.unseal(saved)
    for envelope in ["invalid-json", "[]", '{}', json.dumps({"binding": vault.binding(saved), "credential": 42})]:
        saved.credential_ciphertext = vault.cipher().encrypt(envelope.encode()).decode()
        with pytest.raises(ValidationError):
            vault.unseal(saved)


@pytest.mark.parametrize("extra", [
    {"clear_credential": "on"}, {"data_source_id": "https://localhost/private"},
    {"environment": "staging"}, {"map_title": ""}, {"map_title": "Booking reference"},
    {"map_contact_name": "x" * 101}, {"label": ""},
])
def test_invalid_configuration_has_no_writes_or_echoed_secret(actor, client, extra):
    client.force_login(actor.user)
    response = client.post(url(actor, "integration_new", provider_key="notion"), data(**extra))
    assert response.status_code == 400
    assert SECRET.encode() not in response.content
    assert not IntegrationConnection.objects.exists() and not AdminAuditLog.objects.exists()


def test_duplicate_names_and_attempted_provider_environment_changes(actor):
    saved = save(actor)
    duplicate = service.save_connection(actor.user, actor.profile.school.slug, data(label=saved.label.upper()), provider_key="notion")
    assert "label" in duplicate.errors
    forged = edit_data(actor, saved, environment="live", provider="wix", configuration={"url": "https://evil.invalid"}, enabled="on")
    assert service.save_connection(actor.user, actor.profile.school.slug, forged, pk=saved.pk).is_valid()
    saved.refresh_from_db()
    assert saved.environment == "test" and saved.provider == "notion" and "url" not in saved.configuration
    with pytest.raises(IntegrityError), transaction.atomic():
        IntegrationConnection.objects.create(profile=actor.profile, provider="notion", label=saved.label, environment="test")


def test_source_scoping_role_and_environment(actor, client):
    source = save(actor, "wix", environment="live")
    destination = save(actor)
    foreign = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-connections"), enabled=True)
    alien = IntegrationConnection.objects.create(profile=foreign, provider="wix", environment="test", label="Alien")
    for candidate in (source, destination, alien):
        form = service.save_connection(actor.user, actor.profile.school.slug,
            data(label="Bad source", source=candidate.pk), provider_key="notion")
        assert "source" in form.errors
    assert not IntegrationConnection.objects.filter(label="Bad source").exists()
    with pytest.raises(Http404):
        service.form_for(actor.profile, pk=alien.pk)
    with pytest.raises(Http404):
        service.save_connection(actor.user, actor.profile.school.slug, {}, pk=alien.pk)
    client.force_login(actor.user)
    for name in ("integration_edit", "integration_preview"):
        assert client.get(url(actor, name, pk=alien.pk)).status_code == 404
    assert client.post(url(actor, "integration_edit", pk=alien.pk), data()).status_code == 404
    valid = save(actor, environment="live", label="Live draft", source=source.pk)
    assert valid.source_id == source.pk
    source_form = service.save_connection(actor.user, actor.profile.school.slug,
        edit_data(actor, source, source=alien.pk), pk=source.pk)
    assert source_form.is_valid() and source_form.instance.source_id is None


def test_default_off_and_permissions_csrf_methods(actor, client):
    saved = save(actor)
    client.force_login(actor.user)
    paths = [url(actor, "integrations"), url(actor, "integration_edit", pk=saved.pk),
        url(actor, "integration_new", provider_key="wix"), url(actor, "integration_preview", pk=saved.pk)]
    for role in ("viewer", "scheduler"):
        actor.role = role
        actor.save()
        for path in paths:
            assert client.get(path).status_code == 404
        assert client.post(paths[1], data()).status_code == 404
    actor.role = "owner"
    actor.save()
    assert client.put(paths[1]).status_code == 405 and client.post(paths[0]).status_code == 405
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(actor.user)
    assert csrf.post(paths[1], data()).status_code == 403
    with pytest.raises(ValidationError):
        validate_removal(actor.profile, [key for key in actor.profile.capabilities if key != "integrations"])
    actor.profile.capabilities.remove("integrations")
    actor.profile.save()
    for path in paths:
        assert client.get(path).status_code == 404
    with pytest.raises(Http404):
        service.save_connection(actor.user, actor.profile.school.slug, data(), provider_key="notion")
    assert b">Integrations</a>" not in client.get(url(actor, "workspace")).content


def test_unknown_provider_and_anonymous(actor, client):
    assert client.get(url(actor, "integrations")).status_code == 302
    client.force_login(actor.user)
    assert client.get(url(actor, "integration_new", provider_key="arbitrary-http")).status_code == 404
    with pytest.raises(Http404):
        provider("arbitrary-http")
    actor.profile.enabled = False
    actor.profile.save()
    assert client.get(url(actor, "integrations")).status_code == 404


def test_stale_forged_and_missing_edit_tokens(actor):
    saved = save(actor)
    stale = edit_data(actor, saved)
    assert service.save_connection(actor.user, actor.profile.school.slug, {**stale, "label": "Newest"}, pk=saved.pk).is_valid()
    for revision in (stale["edit_revision"], "forged", ""):
        form = service.save_connection(actor.user, actor.profile.school.slug, {**stale, "edit_revision": revision}, pk=saved.pk)
        assert "edit_revision" in form.errors
    saved.refresh_from_db()
    assert saved.label == "Newest"


def test_audit_failure_rolls_back_connection_and_credential(actor, monkeypatch):
    saved = save(actor)
    before = saved.credential_ciphertext
    def fail(*args, **kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(service, "_audit", fail)
    change = edit_data(actor, saved, label="Should roll back")
    change["credential"] = "replacement"
    with pytest.raises(RuntimeError):
        service.save_connection(actor.user, actor.profile.school.slug, change, pk=saved.pk)
    saved.refresh_from_db()
    assert saved.credential_ciphertext == before and saved.label != "Should roll back"


def test_save_redirect_mapping_preview_and_invalid_saved_config(actor, client):
    client.force_login(actor.user)
    response = client.post(url(actor, "integration_new", provider_key="notion"), data())
    assert response.status_code == 302
    saved = IntegrationConnection.objects.get()
    response = client.get(url(actor, "integration_preview", pk=saved.pk))
    assert response.status_code == 200 and b"Avery: Design: Cat; Color: Blue" in response.content
    assert b"Casey: Design: Bunny; Color: Pink" in response.content
    assert b"No customer data was read or sent" in response.content
    saved.configuration = {}
    saved.save()
    assert b"cannot be previewed" in client.get(url(actor, "integration_preview", pk=saved.pk)).content


def test_projection_preserves_participant_pairing_and_only_mapped_fields():
    adapter = NotionDestination()
    mapping = {**MAPPING, "contact_email": ""}
    projected = adapter.project(example_booking(), {"data_source_id": ID}, mapping)
    payload = projected.payload
    assert projected.api_version == "2026-03-11"
    assert payload["parent"]["data_source_id"] == ID
    properties = payload["properties"]
    assert "Customer email" not in properties and len(properties) == 6
    assert properties[MAPPING["participants"]]["rich_text"][0]["text"]["content"] == "Avery: Design: Cat; Color: Blue\nCasey: Design: Bunny; Color: Pink"
    assert properties[MAPPING["schedule"]]["date"]["end"].endswith("+00:00")
    assert adapter.project(replace(example_booking(), contact_email=""), {"data_source_id": ID}, MAPPING).payload["properties"][MAPPING["contact_email"]]["email"] is None
    assert "staff_notes" not in json.dumps(payload)
    for kind in ("select", "status"):
        result = adapter.project(example_booking(), {"data_source_id": ID, "status_type": kind}, MAPPING)
        assert result.payload["properties"][MAPPING["status"]] == {kind: {"name": "Confirmed"}}
    with pytest.raises(ValidationError):
        adapter.project(example_booking(), {"data_source_id": ID, "status_type": "formula"}, MAPPING)


def test_preview_order_and_multiday_dates_are_unambiguous():
    booking = example_booking()
    booking = replace(booking, ends_at=booking.ends_at + timedelta(days=3))
    result = NotionDestination().project(booking, {"data_source_id": ID}, dict(reversed(list(MAPPING.items()))))
    assert [row["property"] for row in result.rows] == list(MAPPING.values())
    date_row = next(row for row in result.rows if row["kind"] == "date")
    assert "Oct 06, 2026" in date_row["value"] and "Oct 09, 2026" in date_row["value"]
    assert result.payload["properties"][MAPPING["schedule"]]["date"]["end"] == booking.ends_at.isoformat()


@pytest.mark.parametrize("mapping", [None, [], {"unsupported": "Field"}, {**MAPPING, "title": 1}, {**MAPPING, "title": " "}, {**MAPPING, "title": "x" * 101}, {**MAPPING, "title": " customer NAME "}])
def test_invalid_mappings_are_rejected(mapping):
    with pytest.raises(ValidationError):
        validate_mapping(mapping)


@pytest.mark.parametrize("configuration", [{}, {"data_source_id": "bad"}, {"data_source_id": None}, {"data_source_id": 4}])
def test_projection_rejects_invalid_destination(configuration):
    with pytest.raises(ValidationError):
        NotionDestination().project(example_booking(), configuration, MAPPING)


def test_projection_size_time_and_email_boundaries():
    booking = example_booking()
    for malformed in (replace(booking, starts_at=booking.starts_at.replace(tzinfo=None)),
            replace(booking, ends_at=booking.ends_at.replace(tzinfo=None)), replace(booking, ends_at=booking.starts_at),
            replace(booking, contact_email="a" * 201)):
        with pytest.raises(ValidationError):
            NotionDestination().project(malformed, {"data_source_id": ID}, MAPPING)
    assert [len(item["text"]["content"]) for item in rich_text("a" * 2001)] == [2000, 1]
    assert len(rich_text("a" * 200_000)) == 100
    with pytest.raises(ValidationError):
        rich_text("a" * 200_001)
    large = replace(booking, title="a" * 200_000, reference="b" * 200_000, contact_name="c" * 200_000)
    with pytest.raises(ValidationError):
        NotionDestination().project(large, {"data_source_id": ID}, MAPPING)


def test_provider_extension_does_not_change_booking_or_common_views(actor, client, monkeypatch):
    from scheduling.integrations import registry
    class AnotherDestination:
        def project(self, booking, configuration, mapping):
            return Projection({"reference": booking.reference}, ({"property": "Reference", "kind": "text", "value": booking.reference},), "v1")
    spec = Provider("another", "Another tool", "destination", "Token", "https://example.invalid/docs")
    monkeypatch.setattr(registry, "PROVIDERS", {**PROVIDERS, "another": spec})
    monkeypatch.setitem(DESTINATIONS, "another", AnotherDestination())
    saved = IntegrationConnection.objects.create(profile=actor.profile, provider="another", environment="test", label="Adapter proof")
    client.force_login(actor.user)
    response = client.get(url(actor, "integration_preview", pk=saved.pk))
    assert response.status_code == 200 and b"example-booking-001" in response.content
    assert b"API v1" in response.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_edits_accept_one_version(actor):
    saved = save(actor)
    values = edit_data(actor, saved)
    barrier = Barrier(2)
    user_id, slug, pk = actor.user.pk, actor.profile.school.slug, saved.pk
    def attempt(label):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=user_id)
            barrier.wait(timeout=10)
            return service.save_connection(user, slug, {**values, "label": label}, pk=pk).is_valid()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(attempt, label) for label in ("First editor", "Second editor")]
        assert sorted(f.result(timeout=15) for f in results) == [False, True]


@pytest.mark.django_db(transaction=True)
def test_feature_removal_serializes_with_new_connection(actor):
    operator = get_user_model().objects.create_user(username="integration-operator", is_superuser=True)
    token = feature_form(actor.profile)["edit_revision"].value()
    barrier = Barrier(2)
    slug, owner_id, operator_id = actor.profile.school.slug, actor.user.pk, operator.pk
    grants = [key for key in actor.profile.capabilities if key != "integrations"]

    def attempt(removal):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=operator_id if removal else owner_id)
            barrier.wait(timeout=10)
            if removal:
                form = save_features(user, slug, {"capabilities": grants, "edit_revision": token})
                return "removed" if form.is_valid() else "retained"
            try:
                form = service.save_connection(user, slug, data(), provider_key="notion")
                assert form.is_valid(), form.errors
                return "created"
            except Http404:
                return "denied"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, removal) for removal in (True, False)]
        outcomes = {future.result(timeout=15) for future in futures}
    assert outcomes in ({"removed", "denied"}, {"retained", "created"})
    actor.profile.refresh_from_db()
    assert IntegrationConnection.objects.exists() == ("integrations" in actor.profile.capabilities)
