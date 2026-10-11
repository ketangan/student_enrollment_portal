"""Opt-in, single-lesson booking; orders remain owned by core.payments."""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class LessonBookingOffer(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    title = models.CharField(max_length=100)
    service = models.ForeignKey("scheduling.LessonService", on_delete=models.PROTECT)
    teacher = models.ForeignKey("scheduling.LessonTeacher", on_delete=models.PROTECT)
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    room = models.ForeignKey("scheduling.Room", null=True, blank=True, on_delete=models.PROTECT)
    published = models.BooleanField(default=False)
    public_trials = models.BooleanField(default=False)
    family_booking = models.BooleanField(default=False)
    makeup_eligible = models.BooleanField(default=False)
    notice_hours = models.PositiveSmallIntegerField(default=24, validators=[MaxValueValidator(720)])
    horizon_days = models.PositiveSmallIntegerField(default=60, validators=[MinValueValidator(1), MaxValueValidator(180)])
    interval_minutes = models.PositiveSmallIntegerField(default=30, choices=[(15, "15 minutes"), (30, "30 minutes"), (60, "60 minutes")])
    fee_cents = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(100000)])

    class Meta:
        ordering = ["title", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(notice_hours__lte=720,
            horizon_days__gte=1, horizon_days__lte=180, interval_minutes__in=[15, 30, 60], fee_cents__lte=100000),
            name="lesson_offer_limits_valid")]

    @property
    def price(self):
        return Decimal(self.fee_cents) / 100

    def clean(self):
        for name in ("service", "teacher", "location"):
            if getattr(self, name + "_id") and getattr(self, name).profile_id != self.profile_id:
                raise ValidationError({name: "Choose a value in this workspace."})
        if self.room_id and self.room.location_id != self.location_id:
            raise ValidationError({"room": "Choose a room at this location."})
        if self.teacher_id and self.service_id and not self.teacher.services.filter(pk=self.service_id).exists():
            raise ValidationError({"teacher": "This teacher does not offer this service."})
        if self.published and not (self.public_trials or self.family_booking):
            raise ValidationError("Choose at least one booking audience before publishing.")


class LessonBooking(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    offer = models.ForeignKey(LessonBookingOffer, on_delete=models.PROTECT)
    series = models.OneToOneField("scheduling.LessonSeries", on_delete=models.PROTECT, related_name="self_booking")
    grant = models.ForeignKey("scheduling.FamilyScheduleGrant", null=True, on_delete=models.PROTECT)
    order = models.OneToOneField("payments.PaymentOrder", null=True, on_delete=models.PROTECT)
    contact_name = models.CharField(max_length=100)
    contact_email = models.EmailField()
    browser_hash = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


class LessonBookingNotice(models.Model):
    """Durable message preview, committed with the schedule change; never falsely 'sent'."""
    booking = models.ForeignKey(LessonBooking, on_delete=models.PROTECT, related_name="notices")
    delivery = models.OneToOneField("scheduling.BookingMessage", null=True, blank=True, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=80)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["booking", "event_key"], name="lesson_notice_event_unique")]
