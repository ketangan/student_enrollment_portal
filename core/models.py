from dataclasses import dataclass
from django.db import models
import os
import uuid
import base64
import secrets
from django.contrib.auth.models import User
from core.services.form_utils import resolve_label
from django.conf import settings


@dataclass
class SchoolFeatures:
    school: "School"

    def _flags(self) -> dict[str, bool]:
        from core.services.feature_flags import merge_flags
        return merge_flags(plan=self.school.plan, overrides=self.school.feature_flags)

    @property
    def reports_enabled(self) -> bool:
        return bool(self._flags().get("reports_enabled", False))

    @property
    def status_enabled(self) -> bool:
        return bool(self._flags().get("status_enabled", True))

    @property
    def csv_export_enabled(self) -> bool:
        return bool(self._flags().get("csv_export_enabled", True))

    @property
    def audit_log_enabled(self) -> bool:
        return bool(self._flags().get("audit_log_enabled", True))
    

class School(models.Model):
    """
    Multi-tenant anchor. We use ONLY school_slug (Phase 0).
    Branding may be missing; Phase 5 defaults will handle that later.
    """

    
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    website_url = models.URLField(blank=True, default="")
    source_url = models.URLField(blank=True, default="")

    # Feature flags / tiering (school-scoped)
    PLAN_TRIAL = "trial"
    PLAN_STARTER = "starter"
    PLAN_PRO = "pro"
    PLAN_GROWTH = "growth"

    PLAN_CHOICES = [
        (PLAN_TRIAL, "Trial"),
        (PLAN_STARTER, "Starter"),
        (PLAN_PRO, "Pro"),
        (PLAN_GROWTH, "Growth"),
    ]

    plan = models.CharField(
        max_length=32,
        choices=PLAN_CHOICES,
        default=PLAN_TRIAL,
        blank=True,
        db_index=True,
    )
    feature_flags = models.JSONField(default=dict, blank=True)

    # Optional branding fields (can be empty; Phase 5 default applies)
    logo_url = models.CharField(max_length=500, blank=True, default="")
    theme_primary_color = models.CharField(max_length=20, blank=True, default="")
    theme_accent_color = models.CharField(max_length=20, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def features(self) -> "SchoolFeatures":
        return SchoolFeatures(self)

    def save(self, *args, **kwargs):
        from core.services.feature_flags import merge_flags
        if self._state.adding and not (self.feature_flags or {}):
            self.feature_flags = merge_flags(plan=self.plan, overrides={})
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "School"
        verbose_name_plural = "Schools"

    def __str__(self) -> str:
        return self.display_name or self.slug


class SchoolAdminMembership(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="school_membership")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="admin_memberships")

    def __str__(self) -> str:
        return f"{self.user.username} -> {self.school.slug}"



def generate_public_id() -> str:
    """Short, URL-safe identifier for sharing with school admins.

    10 random bytes -> 14 chars base64url (no padding). ~80 bits entropy.
    """

    return base64.urlsafe_b64encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")



class Submission(models.Model):
    """
    Stores a single application submission for a given school.
    The payload is dynamic and will match the YAML-configured fields later (Phase 2+).
    """
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="submissions")

    # Public-facing identifier (safe to share; does not reveal sequential DB ids)
    public_id = models.CharField(
        verbose_name="Application ID",
        max_length=16,
        unique=True,
        db_index=True,
        editable=False,
        blank=True,
        null=False,
        default=generate_public_id,
    )

    form_key = models.CharField(max_length=64, default="default", db_index=True, help_text="Identifies which form was used, in case the school has multiple forms.")

    # JSONB on Postgres automatically; Django uses JSONField
    data = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=40,
        db_index=True,
        default="New",
    )

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
        
        # Enrollment Request Demo (and any other simple “single select program” YAML)
        if data.get("interested_in"):
            raw = data.get("interested_in")
            return resolve_label("interested_in", raw, label_map) or str(raw)

        # Backward-compat if any configs used this older key
        if data.get("program_interest"):
            raw = data.get("program_interest")
            return resolve_label("program_interest", raw, label_map) or str(raw)

        # Multi-form demo (program + optional experience level)
        if data.get("program"):
            program_raw = data.get("program")
            program_label = resolve_label("program", program_raw, label_map) or str(program_raw)

            level_raw = (data.get("experience_level") or "").strip()
            if level_raw:
                level_label = resolve_label("experience_level", level_raw, label_map) or str(level_raw)
                return f"{program_label} ({level_label})"

            return program_label
        
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
    