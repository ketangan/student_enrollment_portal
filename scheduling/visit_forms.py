from django import forms

from .editing import EditRevisionField, protect_edit
from .forms import SessionForm
from .models import Location, Offering, SchedulingMembership


class ProgramForm(forms.ModelForm):
    capture_leads = forms.BooleanField(required=False, label="Connect tour bookings to enrollment leads")
    kind = forms.ChoiceField(required=False, choices=[("tour", "Tour"), ("playdate", "Playdate")], initial="tour", label="Visit type")

    class Meta:
        model = Offering
        fields = ["name", "location", "description", "is_published"]
        labels = {"name": "Visit name", "is_published": "Accept bookings"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, profile, program=None, **kwargs):
        super().__init__(*args, instance=program.offering if program else None, **kwargs)
        self.program = program
        self.fields["location"].queryset = Location.objects.filter(profile=profile)
        self.fields["location"].disabled = bool(program)
        if program:
            self.fields["kind"].initial = program.kind
            self.fields["kind"].disabled = True
            self.fields["capture_leads"].initial = program.capture_leads
            self.fields["bridge_revision"] = EditRevisionField({"program": program.pk, "capture_leads": program.capture_leads})
        protect_edit(self, profile)

    def clean_kind(self):
        return self.cleaned_data["kind"] or "tour"

    def clean(self):
        data = super().clean()
        if data.get("kind") == "playdate" and data.get("capture_leads"):
            self.add_error("capture_leads", "Playdates use the existing application; they do not create another lead.")
        return data


class SlotForm(SessionForm):
    host = forms.ModelChoiceField(queryset=SchedulingMembership.objects.none())
    is_published = forms.BooleanField(required=False, label="Open this time for bookings")

    def __init__(self, *args, profile, program, slot=None, **kwargs):
        super().__init__(*args, offering=program.offering, instance=slot.session if slot else None, **kwargs)
        self.slot = slot
        self.fields["room"].required = True
        self.fields["room"].label = "Room"
        self.fields["capacity"].label = "Total places (adults and children)"
        self.fields["host"].queryset = SchedulingMembership.objects.filter(profile=profile,
            is_active=True, user__is_active=True, role__in=["owner", "scheduler"]).select_related("user")
        self.fields["host"].label_from_instance = lambda member: member.user.get_full_name() or member.user.username
        if slot:
            self.fields["host"].initial = slot.host_id
            self.fields["is_published"].initial = slot.session.is_published
            self.fields["host_revision"] = EditRevisionField({"slot": slot.pk, "host": slot.host_id})
        protect_edit(self, profile)


class BookingForm(forms.Form):
    contact_name = forms.CharField(max_length=100, label="Your name")
    contact_email = forms.EmailField(max_length=254, label="Email")
    child_name = forms.CharField(max_length=100, label="Child's name")
    adults = forms.IntegerField(min_value=1, max_value=10, initial=1, label="Adults attending")
    children = forms.IntegerField(min_value=0, max_value=10, initial=1, label="Children attending")
    authority = forms.BooleanField(label="I am authorized to book for this family.")

    def __init__(self, *args, identity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if identity:
            self.fields["children"] = forms.IntegerField(min_value=1, max_value=10, initial=1, label="Children attending")
            for name, value in identity.items():
                self.fields[name].initial = value
                self.fields[name].disabled = True
