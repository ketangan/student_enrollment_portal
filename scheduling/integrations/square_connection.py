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
from .square_forms import AccountNameForm, MappingForm

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


def selected_connection(profile, connection_id=None):
    records = SquareConnection.objects.select_related("profile__school").filter(
        profile=profile, environment=environment(profile))
    try:
        # Older open pages can still address the sole account, never an arbitrary account.
        return records.get(pk=connection_id) if connection_id else records.get()
    except (SquareConnection.DoesNotExist, SquareConnection.MultipleObjectsReturned, ValidationError):
        raise Http404 from None


@sensitive_variables()
def begin(request, slug, *, connection_id=None, label=None):
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
        if label is not None:
            name_form = AccountNameForm({"label": label})
            if not name_form.is_valid():
                raise ValidationError("Enter an account name of up to 80 characters.")
            connection, _ = SquareConnection.objects.get_or_create(
                profile=profile, environment=app.environment, merchant_id="")
            connection.label = name_form.cleaned_data["label"]
        elif connection_id or SquareConnection.objects.filter(profile=profile, environment=app.environment).exists():
            connection = selected_connection(profile, connection_id)
        else:
            connection = SquareConnection.objects.create(profile=profile, environment=app.environment)
        account_label = connection.label
        connection = SquareConnection.objects.select_for_update().get(pk=connection.pk)
        _available(connection)
        if connection.status == "disconnecting":
            raise ValidationError("Finish disconnecting Square before reconnecting.")
        connection.revision += 1
        connection.label = account_label
        connection.save(update_fields=["revision", "label"])
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
    ready = True
    try:
        _origin(request, application(environment(profile)))
        vault.cipher()
    except (ValidationError, ValueError):
        ready = False
    form = MappingForm(profile, environment(profile))
    return {"accounts": form.connections, "ready": ready, "mapping_form": form,
            "environment": "Sandbox" if profile.school.is_demo else "Production"}


@sensitive_variables()
def credential(connection):
    from core.services.portal_environment import require_deployment_school
    require_deployment_school(connection.profile.school)
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


def update_locations(request, slug, revision, *, connection_id=None):
    """Refresh one seller outside database locks; reject stale results."""
    from core.payments import square
    profile, _ = access(request, slug)
    connection = selected_connection(profile, connection_id)
    if connection.revision != revision:
        raise ValidationError("Square settings changed. Reload before trying again.")
    locations = square.list_locations(connection)
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, _ = access(request, slug)
        current = SquareConnection.objects.select_for_update().get(pk=connection.pk)
        if current.revision != revision or environment(profile) != current.environment:
            raise ValidationError("Square settings changed. Reload before trying again.")
        _available(current)
        current.locations, current.locations_checked_at = locations, timezone.now()
        current.revision += 1
        current.save(update_fields=["locations", "locations_checked_at", "revision"])
        _audit(current, request.user, "square_locations_checked")


def save_mappings(request, slug, data):
    from core.payments import square
    from core.payments.models import PaymentAccount
    from scheduling.models import SquareLocationMapping

    profile, _ = access(request, slug)
    env = environment(profile)
    form = MappingForm(profile, env, data)
    if not form.is_valid():
        raise ValidationError([message for errors in form.errors.values() for message in errors])
    selections = {location.pk: form.cleaned_data[f"location_{location.pk}"] for location in form.locations}
    selected_ids = {value.split(":", 1)[0] for value in selections.values() if value}
    connections = {str(item.pk): item for item in form.connections}
    if not selected_ids <= connections.keys():
        raise ValidationError("Choose a Square account connected to this business.")
    # Remote verification is performed once per selected seller, never inside a lock.
    evidence = {pk: square.list_locations(connections[pk]) for pk in sorted(selected_ids)}
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, _ = access(request, slug)
        list(SquareConnection.objects.select_for_update().filter(profile=profile, environment=env).order_by("pk"))
        current_form = MappingForm(profile, environment(profile), data)
        if not current_form.is_valid():
            raise ValidationError([message for errors in current_form.errors.values() for message in errors])
        current_connections = {str(item.pk): item for item in current_form.connections}
        for pk in selected_ids:
            current = current_connections[pk]
            _available(current)
            credential(current)
        for location_id, value in selections.items():
            if not value:
                SquareLocationMapping.objects.filter(location_id=location_id, environment=env).delete()
                continue
            pk, square_location = value.split(":", 1)
            current = current_connections[pk]
            if square_location not in {item["id"] for item in evidence[pk]}:
                raise ValidationError("Choose an active Square location from this account. Refresh its locations and try again.")
            account, _ = PaymentAccount.objects.get_or_create(school=profile.school, environment=env,
                provider="square", scope="connected", reference=current.merchant_id, location_reference=square_location,
                defaults={"enabled": True})
            SquareLocationMapping.objects.update_or_create(location_id=location_id, environment=env,
                defaults={"account": account, "connection": current})
        for pk in selected_ids:
            current = current_connections[pk]
            current.locations, current.locations_checked_at = evidence[pk], timezone.now()
            current.revision += 1
            current.save(update_fields=["locations", "locations_checked_at", "revision"])
        AdminAuditLog.objects.create(actor=request.user, action="action",
            environment="demo" if env == "test" else "production", model_label=profile._meta.label_lower,
            object_id=str(profile.pk), object_repr="Square location mappings",
            extra={"name": "square_locations_mapped", "scheduling_profile_id": profile.pk})


@transaction.atomic
def rename(request, slug, connection_id, revision, label):
    profile, _ = access(request, slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = access(request, slug)
    connection = selected_connection(profile, connection_id)
    connection = SquareConnection.objects.select_for_update().get(pk=connection.pk)
    if connection.revision != revision:
        raise ValidationError("Square settings changed. Reload before trying again.")
    _available(connection)
    form = AccountNameForm({"label": label})
    if not form.is_valid():
        raise ValidationError("Enter an account name of up to 80 characters.")
    connection.label = form.cleaned_data["label"]
    connection.revision += 1
    connection.save(update_fields=["label", "revision"])
    _audit(connection, request.user, "square_account_renamed")


def due(limit, school=None):
    from core.services.portal_environment import scope_deployment_schools
    records = SquareConnection.objects.exclude(credential_ciphertext="").filter(
        next_check_at__lte=timezone.now()).filter(Q(operation_until=None) | Q(operation_until__lte=timezone.now()))
    records = scope_deployment_schools(records, "profile__school__")
    if school:
        records = records.filter(profile__school__slug=school)
    return list(records.order_by("next_check_at", "pk").values_list("pk", flat=True)[:limit])
