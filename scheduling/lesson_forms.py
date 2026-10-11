from datetime import date, timedelta

from django import forms
from django.utils import timezone
from zoneinfo import ZoneInfo

from .models import Location, Room
from .lesson_models import (LessonService, LessonTeacher, LessonAvailability, LessonClosure,
                            LessonHousehold, LessonStudent)


class ServiceForm(forms.ModelForm):
    class Meta:
        model = LessonService
        fields = ["name", "subject", "duration_minutes", "is_active"]
        labels = {"duration_minutes": "Duration (minutes)", "is_active": "Active"}


class TeacherForm(forms.ModelForm):
    class Meta:
        model = LessonTeacher
        fields = ["name", "services", "buffer_minutes", "travel_minutes", "is_active", "booking_host"]
        labels = {"buffer_minutes": "Gap between lessons (minutes)", "travel_minutes": "Gap when changing locations (minutes)", "booking_host": "Same person as visit host (optional)"}


class AvailabilityForm(forms.ModelForm):
    class Meta:
        model = LessonAvailability
        fields = ["teacher", "location", "weekday", "start_time", "end_time"]
        widgets = {key: forms.TimeInput(attrs={"type": "time"}) for key in ["start_time", "end_time"]}


class ClosureForm(forms.ModelForm):
    class Meta:
        model = LessonClosure
        fields = ["location", "teacher", "first_date", "last_date", "reason"]
        labels = {"teacher": "Teacher (blank means all teachers)"}
        widgets = {key: forms.DateInput(attrs={"type": "date"}) for key in ["first_date", "last_date"]}

    def clean_last_date(self):
        value = self.cleaned_data["last_date"]
        if value == date.max:
            raise forms.ValidationError("Choose an earlier last date.")
        return value


class HouseholdForm(forms.ModelForm):
    class Meta:
        model = LessonHousehold
        fields = ["name", "email"]


class StudentForm(forms.ModelForm):
    class Meta:
        model = LessonStudent
        fields = ["household", "name"]


SETUP_FORMS = {"services": ServiceForm, "teachers": TeacherForm, "availability": AvailabilityForm,
               "closures": ClosureForm, "households": HouseholdForm, "students": StudentForm}


class SeriesForm(forms.Form):
    student = forms.ModelChoiceField(queryset=LessonStudent.objects.none())
    service = forms.ModelChoiceField(queryset=LessonService.objects.none())
    teacher = forms.ModelChoiceField(queryset=LessonTeacher.objects.none())
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    room = forms.ModelChoiceField(queryset=Room.objects.none(), required=False, label="Room (optional)")
    first_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    last_date = forms.DateField(label="Last date (inclusive)", widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    interval_weeks = forms.TypedChoiceField(label="Repeats", choices=[(1, "Every week"), (2, "Every two weeks")], coerce=int)

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = LessonStudent.objects.filter(household__profile=profile).select_related("household")
        self.fields["service"].queryset = LessonService.objects.filter(profile=profile, is_active=True)
        self.fields["teacher"].queryset = LessonTeacher.objects.filter(profile=profile, is_active=True)
        self.fields["location"].queryset = Location.objects.filter(profile=profile)
        self.fields["room"].queryset = Room.objects.filter(location__profile=profile).select_related("location")

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        first, last = data["first_date"], data["last_date"]
        today = timezone.localdate(timezone=ZoneInfo(data["location"].timezone_name))
        if first < today or last < first or last > today + timedelta(days=180):
            raise forms.ValidationError("Choose a future schedule ending within 180 days. The last date must not precede the first.")
        if data["room"] and data["room"].location_id != data["location"].pk:
            raise forms.ValidationError("Choose a room at the selected location.")
        if not data["teacher"].services.filter(pk=data["service"].pk).exists():
            raise forms.ValidationError("This teacher is not assigned to the selected service.")
        return data


class CancelLessonForm(forms.Form):
    scope = forms.ChoiceField(choices=[("one", "Only this lesson"), ("future", "This and future lessons")])
    origin = forms.ChoiceField(label="Requested by", choices=[("family", "Family"), ("studio", "Studio")])
    reason = forms.CharField(max_length=200)
