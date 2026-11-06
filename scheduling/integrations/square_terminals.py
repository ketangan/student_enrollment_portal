"""Device pairing scoped to an existing studio payment account."""
import uuid
import re
from datetime import timedelta
from urllib.parse import quote

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments import square, square_terminal
from scheduling.models import SchedulingProfile, SquareTerminal
from scheduling.reservations import booking_account
from .square_connection import access


class TerminalForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    location = forms.ChoiceField(label="Studio")
    name = forms.CharField(label="Terminal name", max_length=60)

    def __init__(self, profile, data=None):
        super().__init__(data)
        self.fields["location"].choices = [(row.pk, row.name) for row in profile.locations.order_by("name")]
        if profile.school.is_demo:
            self.fields["simulation"] = forms.ChoiceField(label="Sandbox scenario",
                choices=[(key, value[0]) for key, value in square_terminal.SIMULATIONS.items()])


def context(profile, form=None):
    return {"terminal_form": form or TerminalForm(profile), "square_terminals": SquareTerminal.objects.filter(
        location__profile=profile).select_related("location", "account").order_by("location__name", "name")}


def audit(device, user, action):
    AdminAuditLog.objects.create(actor=user, action="action", model_label=device._meta.label_lower,
        object_id=str(device.pk), object_repr=device.name,
        environment="demo" if device.account.environment == "test" else "production",
        extra={"name": action, "scheduling_profile_id": device.location.profile_id})


def add(request, slug, data):
    profile, _ = access(request, slug)
    form = TerminalForm(profile, data)
    if not form.is_valid():
        raise ValidationError([message for errors in form.errors.values() for message in errors])
    values = form.cleaned_data
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, _ = access(request, slug)
        location = get_object_or_404(profile.locations, pk=values["location"])
        account = booking_account(profile, True, location)
        if account.provider != "square":
            raise ValidationError("Connect and select Square for this studio first.")
        existing = SquareTerminal.objects.filter(pk=values["key"]).first()
        if existing:
            if (existing.location_id, existing.account_id, existing.name) != (location.pk, account.pk, values["name"]):
                raise ValidationError("This pairing request has already been used. Reload settings.")
            return existing
        scenario = values.get("simulation")
        device_id = square_terminal.SIMULATIONS[scenario][1] if scenario else ""
        if device_id and SquareTerminal.objects.filter(account=account, device_id=device_id).exists():
            raise ValidationError("This sandbox device is already configured for this Square account.")
        device = SquareTerminal.objects.create(id=values["key"], location=location, account=account,
            name=values["name"], simulation=bool(scenario), device_id=device_id,
            status="paired" if scenario else "pairing")
        audit(device, request.user, "square_terminal_added")
    return device


def check(request, slug, device_id, *, enable=False):
    profile, _ = access(request, slug)
    device = get_object_or_404(SquareTerminal.objects.select_related("account__school", "location"),
        pk=device_id, location__profile=profile)
    if device.simulation:
        if enable:
            with transaction.atomic():
                SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
                access(request, slug)
                if device.account.environment != "test" or not profile.school.is_demo:
                    raise ValidationError("Sandbox devices cannot be used for live payments.")
                SquareTerminal.objects.filter(pk=device.pk).update(status="paired")
                audit(device, request.user, "square_terminal_enabled")
        return ""
    if device.status == "disabled" and not enable:
        return ""
    square.validate_account(device.account)
    if device.code_id:
        result = square.account_request(device.account, "GET", "/devices/codes/" + device.code_id).get("device_code")
    else:
        result = square.account_request(device.account, "POST", "/devices/codes", {
            "idempotency_key": str(device.pk), "device_code": {"name": device.name,
                "location_id": device.account.location_reference, "product_type": "TERMINAL_API"}}).get("device_code")
    if (not isinstance(result, dict) or not square.identifier(result.get("id"))
            or result.get("location_id") != device.account.location_reference
            or result.get("product_type") != "TERMINAL_API"
            or result.get("status") not in {"PAIRED", "UNPAIRED", "EXPIRED", "UNKNOWN"}
            or (device.code_id and device.code_id != result["id"])
            or (result.get("status") == "PAIRED" and not square.identifier(result.get("device_id")))):
        raise ValidationError("Square could not verify this Terminal's pairing or location.")
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        access(request, slug)
        device = SquareTerminal.objects.select_for_update().get(pk=device.pk)
        if device.status == "disabled" and not enable:
            return ""
        identity = result.get("device_id", "")
        if device.device_id and identity != device.device_id:
            raise ValidationError("Terminal identity changed. Pair a new Terminal instead.")
        if identity and SquareTerminal.objects.filter(account=device.account, device_id=identity).exclude(pk=device.pk).exists():
            raise ValidationError("This Terminal is already paired in settings.")
        device.code_id, device.device_id = result["id"], identity
        device.status = {"PAIRED": "paired", "EXPIRED": "expired"}.get(result["status"], "pairing")
        device.save(update_fields=["code_id", "device_id", "status"])
        audit(device, request.user, "square_terminal_checked")
    # Pairing code is shown only to this administrator, never retained in logs.
    code = result.get("code", "")
    return code if device.status == "pairing" and isinstance(code, str) and len(code) <= 20 else ""


@transaction.atomic
def disable(request, slug, device_id):
    profile, _ = access(request, slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    access(request, slug)
    device = get_object_or_404(SquareTerminal, pk=device_id, location__profile=profile)
    device.status = "disabled"
    device.save(update_fields=["status"])
    audit(device, request.user, "square_terminal_disabled")


def ping(request, slug, device_id, *, automatic=False):
    profile, _ = access(request, slug)
    with transaction.atomic():
        device = get_object_or_404(SquareTerminal.objects.select_for_update().select_related("account__school"),
            pk=device_id, location__profile=profile)
        if device.status != "paired" or device.simulation:
            raise ValidationError("Connection checks require a paired physical Terminal.")
        square.validate_account(device.account)
        now = timezone.now()
        active = device.ping_status in {"PENDING", "IN_PROGRESS"}
        if automatic and not active:
            return device
        if device.ping_checked_at and device.ping_checked_at > now - timedelta(seconds=5):
            return device
        if not active or not device.ping_started_at or device.ping_started_at < now - timedelta(minutes=6):
            device.ping_key, device.ping_id = uuid.uuid4(), ""
            device.ping_started_at, device.ping_status = now, "PENDING"
        device.ping_checked_at = now
        device.save(update_fields=["ping_key", "ping_id", "ping_started_at", "ping_status", "ping_checked_at"])
    if device.ping_id:
        result = square.account_request(device.account, "GET", "/terminals/actions/" + quote(device.ping_id, safe="")).get("action")
    else:
        result = square.account_request(device.account, "POST", "/terminals/actions", {
            "idempotency_key": str(device.ping_key),
            "action": {"device_id": device.device_id, "type": "PING", "deadline_duration": "PT1M"}}).get("action")
    if (not isinstance(result, dict) or not isinstance(result.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_:-]{1,255}", result["id"])
            or (device.ping_id and result["id"] != device.ping_id)
            or result.get("type") != "PING" or result.get("device_id") != device.device_id
            or result.get("app_id") != square.environment_setting(device.account.environment, "APPLICATION_ID")
            or result.get("location_id") != device.account.location_reference
            or result.get("status") not in {"PENDING", "IN_PROGRESS", "CANCEL_REQUESTED", "CANCELED", "COMPLETED"}):
        raise ValidationError("Square could not verify this Terminal's connection.")
    SquareTerminal.objects.filter(pk=device.pk, ping_key=device.ping_key, status="paired").update(
        ping_id=result["id"], ping_status=result["status"])
    device.refresh_from_db()
    return device
