"""School-scoped OAuth lifecycle. Provider calls never hold database locks."""
import hashlib
import json
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from core.models import AdminAuditLog
from core.payments.square_oauth import Application
from core.services.portal_environment import request_environment
from scheduling.models import SchedulingProfile, SquareAuthorization, SquareConnection
from . import service, vault

BUSY = "A Square connection update is in progress. Please try again shortly."
RESTART = "This Square connection link has expired or changed. Start again from Integrations."


def environment(profile):
    return "test" if profile.school.is_demo else "live"


def application(env):
    prefix = "PAYMENTS_SQUARE_" + env.upper()
    try:
        return Application(env, getattr(settings, prefix + "_APPLICATION_ID", ""),
            getattr(settings, prefix + "_APPLICATION_SECRET", ""),
            getattr(settings, prefix + "_OAUTH_REDIRECT_URI", ""))
    except ValueError:
        raise ValidationError("Square connection settings need to be checked.") from None


def access(request, slug):
    profile, role = service.access(request.user, slug)
    host_env = request_environment(request)
    if host_env and (host_env == "demo") != profile.school.is_demo:
        raise Http404
    return profile, role


def _origin(request, app):
    target = request.build_absolute_uri(reverse("scheduling:square_oauth_callback"))
    if target != app.redirect_uri:
        raise ValidationError("Square is not configured for this site's callback address.")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _audit(connection, actor, action):
    AdminAuditLog.objects.create(actor=actor, action="action",
        environment="demo" if connection.environment == "test" else "production",
        model_label=connection._meta.label_lower, object_id=str(connection.pk),
        object_repr="Square connection", extra={"name": action, "scheduling_profile_id": connection.profile_id})


def _available(connection):
    if connection.operation_until and connection.operation_until > timezone.now():
        raise ValidationError(BUSY)


def _claim(connection):
    _available(connection)
    connection.operation = uuid.uuid4()
    connection.operation_until = timezone.now() + timedelta(minutes=5)
    connection.revision += 1
    connection.save()
    return connection.operation


@sensitive_variables()
def begin(request, slug):
    profile, _ = access(request, slug)
    app = application(environment(profile))
    _origin(request, app)
    vault.cipher()  # Fail before sending the owner to Square if storage is unavailable.
    if not request.session.session_key:
        request.session.save()
    state = secrets.token_urlsafe(32)
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, _ = access(request, slug)
        if environment(profile) != app.environment:
            raise ValidationError(RESTART)
        connection, _ = SquareConnection.objects.get_or_create(profile=profile, environment=app.environment)
        connection = SquareConnection.objects.select_for_update().get(pk=connection.pk)
        _available(connection)
        if connection.status == "disconnecting":
            raise ValidationError("Finish disconnecting Square before reconnecting.")
        connection.revision += 1
        connection.save(update_fields=["revision"])
        SquareAuthorization.objects.create(state_hash=digest(state), connection=connection, user=request.user,
            session_hash=digest(request.session.session_key), application_id=app.application_id,
            revision=connection.revision, expires_at=timezone.now() + timedelta(minutes=10))
    return app.authorization_url(state)


@sensitive_variables()
def finish(request):
    state = request.GET.get("state", "")
    if not request.user.is_authenticated or not 32 <= len(state) <= 128 or not request.session.session_key:
        raise ValidationError(RESTART)
    with transaction.atomic():
        pending = SquareAuthorization.objects.select_for_update(of=("self",)).filter(state_hash=digest(state),
            user=request.user, session_hash=digest(request.session.session_key), consumed_at=None,
            expires_at__gt=timezone.now()).select_related("connection__profile__school").first()
        if pending is None:
            raise ValidationError(RESTART)
        SchedulingProfile.objects.select_for_update().get(pk=pending.connection.profile_id)
        connection = SquareConnection.objects.select_for_update().get(pk=pending.connection_id)
        profile, _ = access(request, pending.connection.profile.school.slug)
        app = application(connection.environment)
        _origin(request, app)
        if (environment(profile) != connection.environment or pending.revision != connection.revision
                or pending.application_id != app.application_id):
            raise ValidationError(RESTART)
        pending.consumed_at = timezone.now()
        pending.save(update_fields=["consumed_at"])
        if request.GET.get("error"):
            return profile.school.slug, False
        lease = _claim(connection)
    try:
        grant = app.exchange(request.GET.get("code", ""))
        _save_grant(connection.pk, lease, app, grant, request.user)
    except (ValidationError, IntegrityError, Http404):
        _failed(connection.pk, lease)
        raise ValidationError("Square could not be connected. Check the account and try again.") from None
    return profile.school.slug, True


@sensitive_variables()
@transaction.atomic
def _save_grant(pk, lease, app, grant, actor):
    profile_id = SquareConnection.objects.values_list("profile_id", flat=True).get(pk=pk)
    SchedulingProfile.objects.select_for_update().get(pk=profile_id)
    connection = SquareConnection.objects.select_for_update(of=("self",)).select_related("profile__school").get(pk=pk)
    if actor is not None:
        service.access(actor, connection.profile.school.slug)
    if connection.operation != lease or environment(connection.profile) != connection.environment:
        raise ValidationError(RESTART)
    if connection.merchant_id and connection.merchant_id != grant.merchant_id:
        raise ValidationError("Reconnect the original Square account.")
    connection.application_id = app.application_id
    connection.merchant_id = grant.merchant_id
    connection.credential_ciphertext = vault.seal(connection, json.dumps({
        "access_token": grant.access_token, "refresh_token": grant.refresh_token,
        "application_id": app.application_id, "merchant_id": grant.merchant_id}))
    connection.status = "connected"
    connection.expires_at = grant.expires_at
    connection.checked_at = timezone.now()
    connection.next_check_at = min(timezone.now() + timedelta(days=1), grant.expires_at - timedelta(hours=1))
    connection.operation = connection.operation_until = None
    connection.save()
    _audit(connection, actor, "square_connection_verified")


@transaction.atomic
def _failed(pk, lease):
    connection = SquareConnection.objects.select_for_update().get(pk=pk)
    if connection.operation != lease:
        return
    if connection.status != "disconnecting":
        connection.status = "attention" if connection.credential_ciphertext else "disconnected"
    connection.operation = connection.operation_until = None
    connection.next_check_at = timezone.now() + timedelta(hours=1)
    connection.save()
    _audit(connection, None, "square_connection_needs_attention")


@sensitive_variables()
def maintain(pk, *, disconnect=False, actor=None, expected_revision=None):
    with transaction.atomic():
        profile_id = SquareConnection.objects.values_list("profile_id", flat=True).get(pk=pk)
        SchedulingProfile.objects.select_for_update().get(pk=profile_id)
        connection = SquareConnection.objects.select_for_update(of=("self",)).select_related("profile__school").get(pk=pk)
        if expected_revision is not None and connection.revision != expected_revision:
            raise ValidationError("Square settings changed. Reload before trying again.")
        if environment(connection.profile) != connection.environment:
            raise ValidationError("Square connection environment does not match this school.")
        if actor is not None:
            service.access(actor, connection.profile.school.slug)
        if not connection.credential_ciphertext:
            raise ValidationError("Connect a Square account first.")
        lease = _claim(connection)
        disconnect = disconnect or connection.status == "disconnecting"
        if disconnect:
            connection.status = "disconnecting"
            connection.save(update_fields=["status"])
    try:
        app = application(connection.environment)
        credential = json.loads(vault.unseal(connection))
        if (not isinstance(credential, dict) or credential.get("application_id") != app.application_id
                or connection.application_id != app.application_id
                or credential.get("merchant_id") != connection.merchant_id):
            raise ValidationError(RESTART)
        if disconnect:
            app.revoke(credential["access_token"])
            with transaction.atomic():
                current = SquareConnection.objects.select_for_update().get(pk=pk)
                if current.operation != lease:
                    raise ValidationError(RESTART)
                current.credential_ciphertext = ""
                current.status = "disconnected"
                current.expires_at = current.next_check_at = current.operation = current.operation_until = None
                current.checked_at = timezone.now()
                current.save()
                _audit(current, actor, "square_disconnected")
        else:
            grant = app.refresh(credential["refresh_token"], merchant_id=connection.merchant_id)
            _save_grant(pk, lease, app, grant, actor)
    except (ValidationError, ValueError, KeyError, TypeError, IntegrityError, Http404):
        _failed(pk, lease)
        raise ValidationError("Square could not verify the update. Check the connection and retry.") from None


def context(request, profile):
    connection = SquareConnection.objects.filter(profile=profile, environment=environment(profile)).defer("credential_ciphertext").first()
    ready = True
    try:
        _origin(request, application(environment(profile)))
        vault.cipher()
    except (ValidationError, ValueError):
        ready = False
    from django import forms
    mappings = dict(connection.mappings.values_list("location_id", "account__location_reference")) if connection else {}
    choices = [("", "Not mapped")]
    choices += [(item["id"], item["name"]) for item in connection.locations] if connection else []
    form = forms.Form()
    for location in profile.locations.all():
        selected = mappings.get(location.pk, "")
        available = choices + ([(selected, "Unavailable Square location")] if selected and selected not in dict(choices) else [])
        form.fields[f"location_{location.pk}"] = forms.ChoiceField(label=location.name, choices=available, required=False, initial=selected)
    return {"connection": connection, "ready": ready, "mapping_form": form,
            "environment": "Sandbox" if profile.school.is_demo else "Production"}


@sensitive_variables()
def credential(connection):
    if (connection.environment != environment(connection.profile) or connection.status != "connected"
            or not connection.expires_at or connection.expires_at <= timezone.now()):
        raise ValidationError("Reconnect Square in Settings > Integrations before taking payments.")
    app = application(connection.environment)
    try:
        value = json.loads(vault.unseal(connection))
        if (not isinstance(value, dict) or value.get("application_id") != app.application_id
                or connection.application_id != app.application_id or value.get("merchant_id") != connection.merchant_id
                or not isinstance(value.get("access_token"), str) or not value["access_token"]):
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("Reconnect the original Square account.") from None
    return value["access_token"]


def payment_connection(account):
    connection = SquareConnection.objects.select_related("profile__school").filter(
        profile__school_id=account.school_id, environment=account.environment, merchant_id=account.reference).first()
    if connection is None:
        raise ValidationError("The original Square account is not connected.")
    return connection


def update_locations(request, slug, revision, *, mappings=None):
    """Refresh provider evidence outside locks, then apply a versioned mapping atomically."""
    from core.payments import square
    from core.payments.models import PaymentAccount
    from scheduling.models import SquareLocationMapping
    profile, _ = access(request, slug)
    connection = SquareConnection.objects.select_related("profile__school").get(profile=profile, environment=environment(profile))
    if connection.revision != revision:
        raise ValidationError("Square settings changed. Reload before trying again.")
    locations = square.list_locations(connection)
    choices = {item["id"] for item in locations}
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, _ = access(request, slug)
        current = SquareConnection.objects.select_for_update().get(pk=connection.pk)
        if current.revision != revision or environment(profile) != current.environment:
            raise ValidationError("Square settings changed. Reload before trying again.")
        _available(current)
        if mappings is not None:
            location_ids = list(profile.locations.values_list("pk", flat=True))
            expected = {f"location_{pk}" for pk in location_ids}
            if {key for key in mappings if key.startswith("location_")} != expected:
                raise ValidationError("Locations changed. Reload before saving mappings.")
            for pk in location_ids:
                selected = mappings[f"location_{pk}"]
                if selected and selected not in choices:
                    raise ValidationError("Choose an active Square location from this account.")
                if not selected:
                    SquareLocationMapping.objects.filter(connection=current, location_id=pk).delete()
                    continue
                account, _ = PaymentAccount.objects.get_or_create(school=profile.school, environment=current.environment,
                    provider="square", scope="connected", reference=current.merchant_id, location_reference=selected,
                    defaults={"enabled": True})
                SquareLocationMapping.objects.update_or_create(connection=current, location_id=pk, defaults={"account": account})
        current.locations, current.locations_checked_at = locations, timezone.now()
        current.revision += 1
        current.save(update_fields=["locations", "locations_checked_at", "revision"])
        _audit(current, request.user, "square_locations_mapped" if mappings is not None else "square_locations_checked")


def due(limit, school=None):
    records = SquareConnection.objects.exclude(credential_ciphertext="").filter(
        next_check_at__lte=timezone.now()).filter(Q(operation_until=None) | Q(operation_until__lte=timezone.now()))
    if school:
        records = records.filter(profile__school__slug=school)
    return list(records.order_by("next_check_at", "pk").values_list("pk", flat=True)[:limit])
