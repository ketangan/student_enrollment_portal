from django import forms
from django.utils import timezone


def validate_deferred_trial_edit(instance, values):
    """Keep ordinary settings edits out of the explicit conversion/go-live workflow."""
    if not instance.pk or not instance.trial_requires_activation:
        return
    if not instance.is_demo and instance.trial_started_at:
        return
    for name in ("plan", "is_demo", "trial_started_at", "trial_end_date"):
        if name in values and values[name] != getattr(instance, name):
            raise forms.ValidationError(
                "This school's trial starts at written go-live. Use Convert to Customer "
                "for demo conversion, then Record go-live & start trial; do not change "
                "its plan or trial dates during setup."
            )


class TrialActivationForm(forms.Form):
    notice_sent_at = forms.DateTimeField(
        label="Written go-live notice sent at",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "dash-input"}),
    )
    notice_reference = forms.CharField(
        label="Sent email or notice reference", max_length=500,
        widget=forms.TextInput(attrs={"class": "dash-input"}),
    )
    confirmed = forms.BooleanField(label="The agreed workflow is live and written notice has been sent")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["notice_sent_at"].label += f" ({timezone.get_current_timezone_name()})"

    def clean_notice_sent_at(self):
        value = self.cleaned_data["notice_sent_at"]
        if value > timezone.now():
            raise forms.ValidationError("The notice cannot be dated in the future.")
        return value
