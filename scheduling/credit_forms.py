from django import forms

from .credit_models import LessonCreditPolicy
from .lesson_change_forms import RescheduleForm


class CreditPolicyForm(forms.ModelForm):
    class Meta:
        model = LessonCreditPolicy
        fields = ["enabled", "family_cancellations", "studio_cancellations", "notice_hours", "expiry_days"]
        labels = {"enabled": "Issue make-up credits for eligible cancellations",
            "family_cancellations": "Family cancellations qualify",
            "studio_cancellations": "Studio cancellations qualify",
            "notice_hours": "Minimum family notice (hours)",
            "expiry_days": "Use within this many days after the original lesson"}


class RedeemCreditForm(RescheduleForm):
    scope = None
    first_date = forms.DateField(label="Make-up date", widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(label="Start time", widget=forms.TimeInput(attrs={"type": "time"}))


class RevokeCreditForm(forms.Form):
    reason = forms.CharField(max_length=200)
    revision = forms.CharField(widget=forms.HiddenInput, max_length=4096)
    confirm = forms.BooleanField(label="Revoke this unused lesson credit")
