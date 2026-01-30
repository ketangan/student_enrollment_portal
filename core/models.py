from django.db import models
import os
import uuid
from django.contrib.auth.models import User
from core.services.form_utils import resolve_label
from django.conf import settings
from django.db import models


class School(models.Model):
    """
    Multi-tenant anchor. We use ONLY school_slug (Phase 0).
    Branding may be missing; Phase 5 defaults will handle that later.
    """
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    website_url = models.URLField(blank=True, default="")
    source_url = models.URLField(blank=True, default="")

    # Optional branding fields (can be empty; Phase 5 default applies)
    logo_url = models.CharField(max_length=500, blank=True, default="")
    theme_primary_color = models.CharField(max_length=20, blank=True, default="")
    theme_accent_color = models.CharField(max_length=20, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "School"
        verbose_name_plural = "School"  # singular on purpose

    def __str__(self) -> str:
        return self.display_name or self.slug


class SchoolAdminMembership(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="school_membership")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="admin_memberships")

    def __str__(self) -> str:
        return f"{self.user.username} -> {self.school.slug}"



class Submission(models.Model):
    """
    Stores a single application submission for a given school.
    The payload is dynamic and will match the YAML-configured fields later (Phase 2+).
    """
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="submissions")

    # JSONB on Postgres automatically; Django uses JSONField
    data = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.school.slug} submission #{self.id}"
    
    def student_display_name(self) -> str:
        """
        Best-effort extraction of a student/applicant name from dynamic JSON.
        Works across our current YAMLs and is easy to extend later.
        """
        data = self.data or {}

        # Common patterns in our configs
        first = data.get("student_first_name") or data.get("first_name")
        last = data.get("student_last_name") or data.get("last_name")

        if first or last:
            return f"{first or ''} {last or ''}".strip()

        # TSCA
        applicant = data.get("applicant_name")
        if applicant:
            return str(applicant).strip()

        return ""

    def program_display_name(self, label_map: dict | None = None) -> str:
        data = self.data or {}
        label_map = label_map or {}

        # Kimberlas: class_name
        if data.get("class_name"):
            raw = data.get("class_name")
            # Try to convert value -> label using YAML option map
            return resolve_label("class_name", raw, label_map) or str(raw)

        # Dancemaker: dance_style (+ skill_level)
        if data.get("dance_style") and data.get("skill_level"):
            dance = data.get("dance_style")
            level = data.get("skill_level")

            dance_label = resolve_label("dance_style", dance, label_map) or str(dance)
            level_label = resolve_label("skill_level", level, label_map) or str(level)

            return f"{dance_label} ({level_label})"

        if data.get("dance_style"):
            raw = data.get("dance_style")
            return resolve_label("dance_style", raw, label_map) or str(raw)

        # TSCA
        if self.school.slug == "torrance-sister-city-association":
            return "Student Exchange"

        return ""


def submission_upload_path(instance, filename: str) -> str:
    """
    Keep uploads organized by school + submission id.
    Example: uploads/dancemaker-studio/12345/<uuid>__waiver.pdf
    """
    safe_name = os.path.basename(filename or "upload")
    return f"uploads/{instance.submission.school.slug}/{instance.submission_id}/{uuid.uuid4().hex}__{safe_name}"


class SubmissionFile(models.Model):
    submission = models.ForeignKey(
        "Submission",
        on_delete=models.CASCADE,
        related_name="files",
    )

    # Matches the YAML field key (ex: "proof_of_residency")
    field_key = models.CharField(max_length=120)

    file = models.FileField(upload_to=submission_upload_path)

    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=120, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.submission.school.slug} #{self.submission_id} {self.field_key}"
    

class AdminAuditLog(models.Model):
    ACTION_ADD = "add"
    ACTION_CHANGE = "change"
    ACTION_DELETE = "delete"
    ACTION_ACTION = "action"  # e.g. export_csv

    ACTION_CHOICES = (
        (ACTION_ADD, "Add"),
        (ACTION_CHANGE, "Change"),
        (ACTION_DELETE, "Delete"),
        (ACTION_ACTION, "Action"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="admin_audit_logs",
    )

    action = models.CharField(max_length=16, choices=ACTION_CHOICES)

    # What object was affected
    model_label = models.CharField(max_length=128)  # e.g. "core.Submission"
    object_id = models.CharField(max_length=64, blank=True, default="")
    object_repr = models.TextField(blank=True, default="")

    # What changed
    changes = models.JSONField(default=dict, blank=True)  # {"field": {"from": x, "to": y}}

    # Request context
    path = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    # Any extra metadata (counts, filters, etc.)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.created_at} {self.action} {self.model_label}#{self.object_id}"
    