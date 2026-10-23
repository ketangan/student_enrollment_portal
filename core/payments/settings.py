"""Small superadmin control for booking checkout, separate from subscriptions."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse

from core.models import AdminAuditLog, School
from . import square
from .accounts import validate_scope
from .models import PaymentAccount
from .providers import ready


class BookingPaymentForm(forms.Form):
    provider = forms.ChoiceField(label="Payment provider", choices=PaymentAccount.Provider.choices,
                                widget=forms.Select(attrs={"class": "dash-select"}))
    expected_provider = forms.ChoiceField(choices=PaymentAccount.Provider.choices, widget=forms.HiddenInput)


def save_provider(*, school, provider, expected_provider, actor):
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied
    if provider not in PaymentAccount.Provider.values:
        raise ValidationError("Choose Stripe or Square.")
    environment = "test" if school.is_demo else "live"
    if environment == "live" and provider != "square":
        raise ValidationError("Live Stripe booking setup is not supported by this control. Existing Stripe settings are unchanged.")
    # Verify credentials before acquiring database locks. No payment is created here.
    from scheduling.models import SchedulingProfile, SquareConnection, SquareLocationMapping
    connection = SquareConnection.objects.filter(profile__school=school, environment=environment).exclude(merchant_id="").first() if provider == "square" else None
    if environment == "live" and connection is None:
        raise ValidationError("Connect Square and save location mappings in Settings > Integrations first.")
    location = square.verified_location() if provider == "square" and connection is None else None
    with transaction.atomic():
        profile = SchedulingProfile.objects.select_for_update().get(school=school)
        school = School.objects.select_for_update().get(pk=school.pk)
        if ("test" if school.is_demo else "live") != environment or not profile.enabled:
            raise ValidationError("Booking settings changed. Reload before saving.")
        if profile.payment_provider != expected_provider:
            raise ValidationError("Another operator changed the provider. Reload before saving.")
        account = PaymentAccount.objects.filter(school=school, environment=environment, provider=provider).first()
        current_connection = SquareConnection.objects.filter(profile=profile, environment=environment).exclude(merchant_id="").first() if provider == "square" else None
        if (current_connection.pk if current_connection else None) != (connection.pk if connection else None):
            raise ValidationError("Square connection changed. Reload before selecting a provider.")
        if provider == "square" and connection:
            if environment == "live" and not settings.PAYMENTS_SQUARE_LIVE_ENABLED:
                raise ValidationError("Live Square payments have not been activated by Pontora yet. Location mappings can be saved first.")
            mappings = SquareLocationMapping.objects.filter(connection=current_connection, location__profile=profile,
                account__school=school, account__reference=current_connection.merchant_id,
                account__scope="connected", account__provider="square", account__environment=environment).select_related("account__school")
            accounts = [mapping.account for mapping in mappings]
            if not accounts or any(not item.enabled or not ready(item) for item in accounts):
                raise ValidationError("Connect Square and save location mappings in Settings > Integrations first.")
            account = accounts[0]
        elif provider == "square":
            if not settings.DEBUG and not square.webhook_configured():
                raise ValidationError("Connect the Square sandbox webhook before enabling hosted demo checkout.")
            if account and (account.reference != location["merchant_id"] or account.location_reference != location["id"]):
                raise ValidationError("The saved Square account differs from these credentials. Existing payments must keep their original account.")
            if not account:
                account = PaymentAccount.objects.create(school=school, provider="square", scope="platform_demo",
                    reference=location["merchant_id"], location_reference=location["id"], enabled=True)
        if not account or not account.enabled or not ready(account):
            raise ValidationError("Configure and enable this business's sandbox account before selecting it.")
        validate_scope(account)
        previous = profile.payment_provider
        profile.payment_provider = provider
        profile.save(update_fields=["payment_provider"])
        if previous != provider:
            AdminAuditLog.objects.create(actor=actor, environment="demo" if school.is_demo else "production", action="change",
                model_label="core.school", object_id=str(school.pk), object_repr=str(school),
                extra={"name": "booking_payment_provider", "changes": {"payment_provider": {"old": previous, "new": provider}}})


def settings_context(request, school):
    from scheduling.models import SchedulingProfile
    profile = SchedulingProfile.objects.filter(school=school, enabled=True).first()
    is_post = request.method == "POST" and request.POST.get("action") == "save_booking_provider"
    if not profile:
        if is_post:
            raise PermissionDenied
        return None
    form = BookingPaymentForm(request.POST if is_post else None, initial={
        "provider": profile.payment_provider, "expected_provider": profile.payment_provider})
    if is_post and form.is_valid():
        try:
            save_provider(school=school, actor=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Booking payment provider saved. Existing reservations keep their original provider.")
            return redirect(reverse("ops_school_detail", kwargs={"slug": school.slug}) + "#booking-payments")
    return {"form": form, "provider": profile.get_payment_provider_display(),
            "environment": "Sandbox" if school.is_demo else "Live"}
