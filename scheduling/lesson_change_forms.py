from django import forms

from .lesson_models import LessonOccurrence, LessonTeacher
from .models import Location, Room


class RescheduleForm(forms.Form):
    scope = forms.ChoiceField(choices=[("one", "Only this lesson"), ("future", "This and later scheduled lessons")])
    first_date = forms.DateField(label="New date for this lesson", widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(label="New start time", widget=forms.TimeInput(attrs={"type": "time"}))
    teacher = forms.ModelChoiceField(queryset=LessonTeacher.objects.none())
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    room = forms.ModelChoiceField(queryset=Room.objects.none(), required=False, label="Room (optional)")
    reason = forms.CharField(max_length=200)

    def __init__(self, *args, occurrence, **kwargs):
        super().__init__(*args, **kwargs)
        self.occurrence = occurrence
        profile = occurrence.series.profile
        self.fields["teacher"].queryset = LessonTeacher.objects.filter(profile=profile, is_active=True)
        self.fields["location"].queryset = Location.objects.filter(profile=profile)
        self.fields["room"].queryset = Room.objects.filter(location__profile=profile).select_related("location")

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if data["room"] and data["room"].location_id != data["location"].pk:
            raise forms.ValidationError("Choose a room at the selected location.")
        if not data["teacher"].services.filter(pk=self.occurrence.series.service_id).exists():
            raise forms.ValidationError("This teacher is not assigned to the lesson service.")
        return data


class AttendanceForm(forms.Form):
    attendance = forms.ChoiceField(choices=LessonOccurrence.ATTENDANCE_CHOICES)
    reason = forms.CharField(max_length=200)
    revision = forms.CharField(widget=forms.HiddenInput)
