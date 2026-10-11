from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Camp


def validate_dates(camp, sessions, opening=False):
    if not 2 <= len(sessions) <= 31:
        raise ValidationError("Choose between 2 and 31 fixed sessions.")
    if any(s.offering_id != camp.offering_id for s in sessions):
        raise ValidationError("Every session must belong to this workshop.")
    if any(first.ends_at > second.starts_at for first, second in zip(sessions, sessions[1:])):
        raise ValidationError("Camp sessions cannot overlap.")
    if opening and (not camp.offering.is_published or any(not s.is_published or s.starts_at <= timezone.now() for s in sessions)):
        raise ValidationError("Publish the workshop and every future session before opening registration.")


class SessionChoices(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        zone = ZoneInfo(obj.offering.location.timezone_name)
        start, end = timezone.localtime(obj.starts_at, zone), timezone.localtime(obj.ends_at, zone)
        return f"{start:%a, %b %d, %Y, %I:%M %p} - {end:%I:%M %p} | {obj.room.name if obj.room else 'No room'} | {obj.capacity} places"


class CampForm(forms.ModelForm):
    class Meta:
        model = Camp
        fields = ["name", "description", "sessions", "price", "deposit", "online", "is_open"]
        labels = {"price": "Full camp price per participant (USD)", "deposit": "Deposit per participant (USD; 0 means full payment)",
                  "online": "Use Stripe test checkout", "is_open": "Open registration"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, offering, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.offering = offering
        self.fields["sessions"] = SessionChoices(queryset=offering.sessions.select_related("offering__location", "room"), widget=forms.CheckboxSelectMultiple)
        if self.instance.pk and self.instance.registrations.exists():
            for name in ("sessions", "price", "deposit", "online"):
                self.fields[name].disabled = True

    def clean(self):
        data = super().clean()
        price, deposit = data.get("price"), data.get("deposit")
        if data.get("online") and price is not None and deposit is not None:
            from decimal import Decimal
            if any(0 < amount < Decimal("0.50") for amount in (price, deposit, price - deposit)):
                raise ValidationError("Each nonzero test payment, including the remaining balance, must be at least $0.50 per participant.")
        if "sessions" in data:
            validate_dates(self.instance, list(data["sessions"]), opening=data.get("is_open", False))
        return data
