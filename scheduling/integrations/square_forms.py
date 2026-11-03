"""School-wide Square mappings with the shared setup edit protection."""
import hashlib
import json

from django import forms

from scheduling.editing import EditRevisionField
from scheduling.models import SquareConnection, SquareLocationMapping


class AccountNameForm(forms.Form):
    label = forms.CharField(label="Account name", max_length=80)


class MappingForm(forms.Form):
    def __init__(self, profile, environment, data=None):
        super().__init__(data)
        self.connections = list(SquareConnection.objects.filter(profile=profile, environment=environment)
                                .defer("credential_ciphertext").order_by("label", "merchant_id", "pk"))
        self.locations = list(profile.locations.order_by("name", "pk"))
        mappings = {row.location_id: row for row in SquareLocationMapping.objects.filter(
            location__profile=profile, environment=environment).select_related("account")}
        snapshot = {
            "profile": profile.pk, "environment": environment,
            "connections": sorted((str(row.pk), row.revision) for row in self.connections),
            "locations": sorted(row.pk for row in self.locations),
            "mappings": sorted((pk, str(row.connection_id), row.account_id) for pk, row in mappings.items()),
        }
        self.fields["edit_revision"] = EditRevisionField({
            "square_mappings": hashlib.sha256(json.dumps(snapshot, default=str).encode()).hexdigest()})
        choices = [("", "Not mapped")]
        available = set()
        for connection in self.connections:
            options = [(f"{connection.pk}:{item['id']}", f"{connection.display_name} / {item['name']}") for item in connection.locations]
            if options:
                choices.append((connection.display_name, options))
                available.update(value for value, _ in options)
        for location in self.locations:
            mapping = mappings.get(location.pk)
            selected = f"{mapping.connection_id}:{mapping.account.location_reference}" if mapping else ""
            options = choices + ([(selected, "Unavailable Square location")] if selected and selected not in available else [])
            self.fields[f"location_{location.pk}"] = forms.ChoiceField(
                label=location.name, choices=options, required=False, initial=selected)

    def clean(self):
        cleaned = super().clean()
        if {key for key in self.data if key.startswith("location_")} != {f"location_{row.pk}" for row in self.locations}:
            raise forms.ValidationError("Locations changed. Reload before saving mappings.")
        return cleaned
