from decimal import Decimal

from django import forms

from .appointment_models import LessonBookingOffer
from .editing import protect_edit
from .lesson_models import LessonService, LessonTeacher, LessonStudent
from .models import Location, Room


class OfferForm(forms.ModelForm):
    price = forms.DecimalField(min_value=0, max_value=1000, decimal_places=2, initial=0,
        label="Price (USD, collected by studio)")

    class Meta:
        model = LessonBookingOffer
        fields = ["title", "service", "teacher", "location", "room", "public_trials", "family_booking",
            "makeup_eligible", "notice_hours", "horizon_days", "interval_minutes", "published"]
        labels = {"public_trials": "Allow public trial bookings", "family_booking": "Allow authorized families to book",
            "makeup_eligible": "Apply the studio's make-up credit policy to these bookings",
            "notice_hours": "Minimum booking notice (hours)", "horizon_days": "Book up to this many days ahead",
            "interval_minutes": "Start times every", "published": "Publish this offer"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.profile = profile
        for name, model, path in [("service", LessonService, "profile"), ("teacher", LessonTeacher, "profile"),
                                 ("location", Location, "profile"), ("room", Room, "location__profile")]:
            self.fields[name].queryset = model.objects.filter(**{path: profile})
        self.fields["price"].initial = self.instance.price
        protect_edit(self, profile)

    def clean(self):
        data = super().clean()
        if "price" in data:
            self.instance.fee_cents = int(data["price"] * 100)
        return data


class AvailabilityForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    student = forms.ModelChoiceField(queryset=LessonStudent.objects.none(), required=False)

    def __init__(self, *args, students=None, **kwargs):
        super().__init__(*args, **kwargs)
        if students is None:
            del self.fields["student"]
        else:
            self.fields["student"].queryset = students
            self.fields["student"].required = True


class TrialContactForm(forms.Form):
    contact_name = forms.CharField(max_length=100, label="Your name")
    contact_email = forms.EmailField(max_length=254, label="Your email")
    student_name = forms.CharField(max_length=100, label="Student name")
    authority = forms.BooleanField(label="I am booking for myself or a child I am authorized to book for.")


class OfflineLessonPaymentForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput)
    kind = forms.ChoiceField(choices=[("payment", "Payment received"), ("refund", "Refund issued")])
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_value=1000, decimal_places=2, label="Amount (USD)")
    reference = forms.CharField(max_length=100, label="Receipt / reference")
    confirm = forms.BooleanField(label="I am recording a local test payment, not charging a customer.")
