"""Optimistic edit checks, validated inside each setup writer's tenant lock."""

import hashlib
import json

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder

EDIT_SALT = "scheduling.setup-edit.v1"
EDIT_MAX_AGE = 86400
EDIT_ERROR = "These settings may have changed or this form has expired. Reload this page before saving. Your changes have not been saved."


class EditRevisionField(forms.CharField):
    def __init__(self, expected):
        self.expected = expected
        super().__init__(widget=forms.HiddenInput, max_length=2048,
                         initial=signing.dumps(expected, salt=EDIT_SALT),
                         error_messages={"required": EDIT_ERROR, "max_length": EDIT_ERROR})

    def validate(self, value):
        super().validate(value)
        try:
            saved = signing.loads(value, salt=EDIT_SALT, max_age=EDIT_MAX_AGE)
        except signing.BadSignature as exc:
            raise ValidationError(EDIT_ERROR, code="stale_edit") from exc
        if saved != self.expected:
            raise ValidationError(EDIT_ERROR, code="stale_edit")

    def has_changed(self, initial, data):
        return False


def protect_edit(form, profile):
    instance = form.instance
    if instance._state.adding:
        return form
    state = {field.attname: field.value_from_object(instance) for field in instance._meta.concrete_fields}
    # Use the relations actually loaded for display, not a second, possibly newer query.
    for field in instance._meta.many_to_many:
        state[field.name] = sorted(str(getattr(value, "pk", value)) for value in form.initial.get(field.name, []))
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()
    form.fields["edit_revision"] = EditRevisionField({
        "profile": profile.pk, "model": instance._meta.label_lower, "pk": str(instance.pk), "state": digest,
    })
    return form
