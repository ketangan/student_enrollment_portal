from django import forms
from django.core.exceptions import ValidationError

from scheduling.models import IntegrationConnection
from .payloads import MAPPING_FIELDS, validate_mapping
from .registry import PROVIDERS, provider


class ConnectionForm(forms.ModelForm):
    credential = forms.CharField(required=False, max_length=4096, strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the saved credential.")
    clear_credential = forms.BooleanField(required=False, label="Remove saved credential")

    class Meta:
        model = IntegrationConnection
        fields = ["label", "environment", "source"]
        labels = {"label": "Connection name", "environment": "Intended use", "source": "Bookings come from"}
        help_texts = {"environment": "Testing and production records are separate. Neither is activated by saving."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        connection = self.instance
        self.spec = provider(connection.provider)
        if not connection._state.adding:
            self.fields["environment"].disabled = True
        self.fields["credential"].label = self.spec.credential_label
        self.fields["source"].queryset = IntegrationConnection.objects.filter(
            profile=connection.profile, provider__in=[key for key, spec in PROVIDERS.items() if spec.role == "source"])
        self.fields["source"].empty_label = "Pontora Bookings"
        if self.spec.role != "destination":
            del self.fields["source"]

    @property
    def settings_fields(self):
        return [field for field in self.visible_fields() if not field.name.startswith("map_")]

    @property
    def mapping_fields(self):
        return [field for field in self.visible_fields() if field.name.startswith("map_")]

    def clean(self):
        clean = super().clean()
        if IntegrationConnection.objects.filter(profile=self.instance.profile,
                environment=clean.get("environment"), label__iexact=clean.get("label", "")).exclude(pk=self.instance.pk).exists():
            self.add_error("label", "A connection with this name already exists for this intended use.")
        if clean.get("credential") and clean.get("clear_credential"):
            self.add_error("credential", "Enter a replacement or remove the credential, not both.")
        source = clean.get("source")
        if source and source.environment != clean.get("environment"):
            self.add_error("source", "Choose a booking source with the same intended use.")
        return clean


class WixConnectionForm(ConnectionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site_id"] = forms.UUIDField(label="Wix site ID", initial=self.instance.configuration.get("site_id"))
        self.fields["visitor_client_id"] = forms.UUIDField(label="Visitor OAuth client ID", required=False,
            initial=self.instance.configuration.get("visitor_client_id"), help_text="Optional until the customer booking flow is configured.")
        self.order_fields(["label", "environment", "site_id", "visitor_client_id", "credential", "clear_credential"])

    def clean(self):
        clean = super().clean()
        self.instance.configuration = {key: str(clean[key]) for key in ("site_id", "visitor_client_id") if clean.get(key)}
        self.instance.field_mapping = {}
        self.instance.source = None
        return clean


class NotionConnectionForm(ConnectionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data_source_id"] = forms.UUIDField(label="Notion data source ID",
            initial=self.instance.configuration.get("data_source_id"))
        self.fields["status_type"] = forms.ChoiceField(label="Notion booking-status field type",
            choices=[("rich_text", "Text"), ("select", "Select"), ("status", "Status")],
            initial=self.instance.configuration.get("status_type", "rich_text"))
        for key, (label, kind, required) in MAPPING_FIELDS.items():
            self.fields[f"map_{key}"] = forms.CharField(label=label, required=required, max_length=100,
                initial=self.instance.field_mapping.get(key, ""), help_text=("Notion property name or ID; uses the status type selected above."
                    if key == "status" else f"Notion property name or ID; type: {kind}."))
        self.order_fields(["label", "environment", "source", "data_source_id", "status_type", "credential", "clear_credential",
            *[f"map_{key}" for key in MAPPING_FIELDS]])

    def clean(self):
        clean = super().clean()
        self.instance.configuration = {"data_source_id": str(clean["data_source_id"])} if clean.get("data_source_id") else {}
        self.instance.configuration["status_type"] = clean.get("status_type", "rich_text")
        mapping = {key: clean.get(f"map_{key}", "") for key in MAPPING_FIELDS}
        try:
            validate_mapping(mapping)
        except ValidationError as exc:
            self.add_error(None, exc)
        self.instance.field_mapping = mapping
        return clean


FORMS = {"wix": WixConnectionForm, "notion": NotionConnectionForm}
