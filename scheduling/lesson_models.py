"""Private, staff-assigned lesson schedules. Billing is deliberately separate."""
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower


class LessonService(models.Model):
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=80, verbose_name="Instrument / subject")
    duration_minutes = models.PositiveSmallIntegerField(validators=[MinValueValidator(10), MaxValueValidator(180)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "profile", name="lesson_service_name_unique"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gte=10, duration_minutes__lte=180), name="lesson_duration_valid"),
        ]

    def __str__(self):
        return f"{self.name} ({self.duration_minutes} min)"


class LessonTeacher(models.Model):
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    services = models.ManyToManyField(LessonService)
    buffer_minutes = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(120)])
    travel_minutes = models.PositiveSmallIntegerField(default=30, validators=[MaxValueValidator(240)])
    is_active = models.BooleanField(default=True)
    booking_host = models.OneToOneField("scheduling.SchedulingMembership", null=True, blank=True,
        on_delete=models.PROTECT, related_name="lesson_teacher")

    def clean(self):
        if self.booking_host_id:
            if self.booking_host.profile_id != self.profile_id:
                raise ValidationError("The visit host must belong to this workspace.")
            from .visit_resources import validate_teacher_mapping
            validate_teacher_mapping(self)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "profile", name="lesson_teacher_name_unique"),
            models.CheckConstraint(condition=models.Q(buffer_minutes__lte=120, travel_minutes__lte=240), name="lesson_teacher_buffers_valid"),
        ]

    def __str__(self):
        return self.name


class LessonAvailability(models.Model):
    teacher = models.ForeignKey(LessonTeacher, on_delete=models.PROTECT, related_name="availability")
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    weekday = models.PositiveSmallIntegerField(choices=list(enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])))
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["teacher__name", "weekday", "start_time", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(weekday__lte=6), name="lesson_availability_day_valid"),
            models.CheckConstraint(condition=models.Q(end_time__gt=models.F("start_time")), name="lesson_availability_time_valid"),
        ]

    def clean(self):
        if self.teacher_id and self.location_id and self.teacher.profile_id != self.location.profile_id:
            raise ValidationError("Teacher and location must belong to the same workspace.")


class LessonClosure(models.Model):
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    teacher = models.ForeignKey(LessonTeacher, null=True, blank=True, on_delete=models.PROTECT)
    first_date = models.DateField()
    last_date = models.DateField()
    reason = models.CharField(max_length=120)

    class Meta:
        ordering = ["first_date", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(last_date__gte=models.F("first_date")), name="lesson_closure_dates_valid")]

    def clean(self):
        if self.teacher_id and self.location_id and self.teacher.profile_id != self.location.profile_id:
            raise ValidationError("Teacher and location must belong to the same workspace.")


class LessonHousehold(models.Model):
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.CASCADE)
    name = models.CharField(max_length=100, verbose_name="Family / contact name")
    email = models.EmailField()

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class LessonStudent(models.Model):
    household = models.ForeignKey(LessonHousehold, on_delete=models.PROTECT, related_name="students")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.name} - {self.household.name}"


class LessonSeries(models.Model):
    predecessor = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replacements")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    service = models.ForeignKey(LessonService, on_delete=models.PROTECT)
    teacher = models.ForeignKey(LessonTeacher, on_delete=models.PROTECT)
    student = models.ForeignKey(LessonStudent, on_delete=models.PROTECT)
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    room = models.ForeignKey("scheduling.Room", null=True, blank=True, on_delete=models.PROTECT)
    first_date = models.DateField()
    last_date = models.DateField()
    start_time = models.TimeField()
    interval_weeks = models.PositiveSmallIntegerField(default=1, choices=[(1, "Every week"), (2, "Every two weeks")])
    snapshot = models.JSONField(default=dict)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(last_date__gte=models.F("first_date")), name="lesson_series_dates_valid"),
            models.CheckConstraint(condition=models.Q(interval_weeks__in=[1, 2]), name="lesson_series_interval_valid"),
        ]


class LessonOccurrence(models.Model):
    ATTENDANCE_CHOICES = [("unmarked", "Unmarked"), ("present", "Present"), ("absent", "Absent"), ("no_show", "No-show")]
    series = models.ForeignKey(LessonSeries, on_delete=models.PROTECT, related_name="occurrences")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    state = models.CharField(max_length=12, choices=[("scheduled", "Scheduled"), ("skipped", "Closed"), ("canceled", "Canceled"), ("rescheduled", "Rescheduled")])
    reason = models.CharField(max_length=200, blank=True)
    replacement = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="replaces")
    attendance = models.CharField(max_length=10, choices=ATTENDANCE_CHOICES, default="unmarked")
    attendance_note = models.CharField(max_length=200, blank=True)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["series", "starts_at"], name="lesson_occurrence_unique"),
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F("starts_at")), name="lesson_occurrence_time_valid"),
            models.CheckConstraint(condition=models.Q(state__in=["scheduled", "skipped", "canceled", "rescheduled"]), name="lesson_occurrence_state_valid"),
            models.CheckConstraint(condition=models.Q(attendance__in=["unmarked", "present", "absent", "no_show"]), name="lesson_attendance_valid"),
            models.CheckConstraint(condition=models.Q(state="scheduled") | models.Q(attendance="unmarked"), name="lesson_attendance_state_valid"),
            models.CheckConstraint(condition=(models.Q(state="rescheduled", replacement__isnull=False) | (~models.Q(state="rescheduled") & models.Q(replacement__isnull=True))), name="lesson_replacement_state_valid"),
        ]
        indexes = [models.Index(fields=["starts_at", "ends_at"], name="lesson_occurrence_times")]
