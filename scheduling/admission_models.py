"""Opt-in enrollment connection; standalone visits need neither policy nor case."""
from django.db import models


class AdmissionPolicy(models.Model):
    profile = models.OneToOneField("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    require_tour = models.BooleanField(default=True)
    application_release = models.CharField(max_length=12, default="automatic",
        choices=[("automatic", "Automatically when eligible"), ("staff", "After staff approval")])
    playdate_release = models.CharField(max_length=12, default="staff",
        choices=[("automatic", "Automatically when eligible"), ("staff", "After staff approval")])
    eligible_statuses = models.JSONField(default=list)
    scheduled_status = models.CharField(max_length=40, default="Playdate Scheduled")
    completed_status = models.CharField(max_length=40, default="Playdate Completed")


class AdmissionCase(models.Model):
    policy = models.ForeignKey(AdmissionPolicy, on_delete=models.PROTECT, related_name="cases")
    lead = models.OneToOneField("core.Lead", on_delete=models.PROTECT)
    tour = models.OneToOneField("scheduling.VisitBooking", null=True, blank=True,
        on_delete=models.PROTECT, related_name="tour_admission")
    draft = models.OneToOneField("core.DraftSubmission", null=True, blank=True, on_delete=models.PROTECT)
    submission = models.OneToOneField("core.Submission", null=True, blank=True, on_delete=models.PROTECT)
    application_released_at = models.DateTimeField(null=True, blank=True)
    playdate_released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
