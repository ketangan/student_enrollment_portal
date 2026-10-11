from django import forms

from .editing import protect_edit
from .models import SchedulingProfile


class MessageSettingsForm(forms.ModelForm):
    class Meta:
        model = SchedulingProfile
        fields = ["message_preview_enabled"]
        labels = {"message_preview_enabled": "Automatically queue new confirmations for local testing"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        protect_edit(self, self.instance)


class ProcessMessagesForm(forms.Form):
    limit = forms.IntegerField(min_value=1, max_value=20, initial=5, label="Maximum messages")
    outcome = forms.ChoiceField(choices=[("success", "Successful local delivery"),
        ("retry", "Temporary rejection (retry later)"), ("uncertain", "Unknown outcome (requires review)")],
        label="Test outcome")
