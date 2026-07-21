from dataclasses import dataclass
from datetime import timedelta, datetime, time
from math import ceil as _ceil
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone
import os
import re
import uuid
import base64
import secrets
from django.contrib.auth.models import User
from core.services.form_utils import resolve_label
from core.services import feature_flags as ff
from core.services.admin_themes import THEME_CHOICES, DEFAULT_THEME_KEY
from django.conf import settings

# How many days a trial lasts. Single source of truth — do not hardcode elsewhere.
TRIAL_LENGTH_DAYS = 30

# Show the trial countdown banner only when this many days (or fewer) remain.
# 0 = show only after expiry; set higher to warn earlier.
TRIAL_BANNER_THRESHOLD_DAYS = 10


def generate_submission_status_token() -> str:
    """Top-level callable so Django's migration framework can serialize it."""
    return secrets.token_urlsafe(32)


@dataclass
class SchoolFeatures:
    school: "School"

    def _flags(self) -> dict[str, bool]:
        # Cache per-instance to avoid recomputing on every property access.
        # Effective because School.features caches the SchoolFeatures instance.
        cached = getattr(self, "_cached_flags", None)
        if cached is not None:
            return cached
        flags = ff.merge_flags(plan=self.school.plan, overrides=self.school.feature_flags)
        self._cached_flags = flags
        return flags

    # All defaults below are False (deny by default).  _flags() always
    # contains every key from _FEATURE_MIN_PLAN, so the fallback never
    # fires for known flags.  False ensures that an unregistered flag
    # silently stays off rather than accidentally enabling a feature.

    @property
    def reports_enabled(self) -> bool:
        return bool(self._flags().get("reports_enabled", False))

    @property
    def status_enabled(self) -> bool:
        return bool(self._flags().get("status_enabled", False))

    @property
    def csv_export_enabled(self) -> bool:
        return bool(self._flags().get("csv_export_enabled", False))

    @property
    def audit_log_enabled(self) -> bool:
        return bool(self._flags().get("audit_log_enabled", False))

    @property
    def email_notifications_enabled(self) -> bool:
        return bool(self._flags().get("email_notifications_enabled", False))

    @property
    def file_uploads_enabled(self) -> bool:
        return bool(self._flags().get("file_uploads_enabled", False))

    @property
    def custom_branding_enabled(self) -> bool:
        return bool(self._flags().get("custom_branding_enabled", False))

    @property
    def multi_form_enabled(self) -> bool:
        return bool(self._flags().get("multi_form_enabled", False))

    @property
    def custom_statuses_enabled(self) -> bool:
        return bool(self._flags().get("custom_statuses_enabled", False))

    @property
    def leads_enabled(self) -> bool:
        return bool(self._flags().get("leads_enabled", False))

    @property
    def leads_conversion_enabled(self) -> bool:
        return bool(self._flags().get("leads_conversion_enabled", False))

    @property
    def waiver_enabled(self) -> bool:
        return bool(self._flags().get("waiver_enabled", False))

    @property
    def save_resume_enabled(self) -> bool:
        return bool(self._flags().get("save_resume_enabled", False))

    @property
    def ai_summary_enabled(self) -> bool:
        return bool(self._flags().get("ai_summary_enabled", False))

    @property
    def family_portal_enabled(self) -> bool:
        return bool(self._flags().get("family_portal_enabled", False))


class School(models.Model):
    """
    Multi-tenant anchor. We use ONLY school_slug (Phase 0).
    Branding may be missing; Phase 5 defaults will handle that later.
    """

    
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=255, blank=True, default="")
    website_url = models.URLField(blank=True, default="")
    source_url = models.URLField(blank=True, default="")

    plan = models.CharField(
        max_length=32,
        choices=ff.PLAN_CHOICES,
        default=ff.PLAN_TRIAL,
        blank=True,
        db_index=True,
    )
    feature_flags = models.JSONField(default=dict, blank=True)

    # Optional branding fields (can be empty; Phase 5 default applies)
    logo_url = models.CharField(max_length=500, blank=True, default="")
    theme_primary_color = models.CharField(max_length=20, blank=True, default="")
    theme_accent_color = models.CharField(max_length=20, blank=True, default="")


    # Trial billing
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_end_date = models.DateField(null=True, blank=True)

    # Stripe billing (platform subscription)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_status = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_demo = models.BooleanField(default=False, db_index=True)
    activity_tracking_enabled = models.BooleanField(default=False)
    stripe_cancel_at = models.DateTimeField(null=True, blank=True)
    stripe_cancel_at_period_end = models.BooleanField(default=False)
    stripe_current_period_end = models.DateTimeField(null=True, blank=True)

    # Per-school Stripe keys for collecting application fees directly from applicants.
    # These are the school's OWN Stripe account keys, not the platform's keys.
    app_fee_stripe_public_key = models.CharField(max_length=255, blank=True, default="")
    app_fee_stripe_secret_key = models.CharField(max_length=255, blank=True, default="")

    # DB-driven programs: when set, this field key's options come from SchoolProgram records, not YAML.
    program_field_key = models.CharField(max_length=120, blank=True, default="")

    # Webhook token for external lead intake. Generate via ensure_lead_webhook_token(); never auto-set in save().
    lead_webhook_token = models.CharField(max_length=64, blank=True, default="")

    # Per-school SMTP relay. When smtp_host is set, outbound emails use these credentials
    # so they appear in the school's own sent folder rather than coming from Resend.
    smtp_host = models.CharField(max_length=255, blank=True, default="")
    smtp_port = models.PositiveIntegerField(null=True, blank=True)
    smtp_username = models.CharField(max_length=255, blank=True, default="")
    smtp_password = models.CharField(max_length=255, blank=True, default="")
    smtp_from_email = models.EmailField(max_length=255, blank=True, default="")
    smtp_use_tls = models.BooleanField(default=True)

    # Default days before a follow-up is due after marking a lead/submission contacted.
    default_follow_up_days = models.PositiveSmallIntegerField(default=2)

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def features(self) -> "SchoolFeatures":
        cached = getattr(self, "_features_cache", None)
        if cached is None:
            cached = SchoolFeatures(self)
            self._features_cache = cached
        return cached

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using=using, fields=fields, from_queryset=from_queryset)
        self.__dict__.pop("_features_cache", None)

    # ── Trial helpers ──────────────────────────────────────────────────────

    @property
    def is_trial_plan(self) -> bool:
        return self.plan == ff.PLAN_TRIAL

    @property
    def trial_ends_at(self):
        """UTC datetime when the trial expires, or None if not a trial school."""
        if not self.is_trial_plan or not self.trial_started_at:
            return None
        if self.trial_end_date:
            # Superadmin override: school gets the full override date (23:59:59 UTC)
            return timezone.make_aware(
                datetime.combine(self.trial_end_date, time(23, 59, 59))
            )
        return self.trial_started_at + timedelta(days=TRIAL_LENGTH_DAYS)

    @property
    def trial_days_left(self) -> int:
        """Days remaining in trial, clamped to 0. Ceiling so "expires today" shows 1."""
        ends_at = self.trial_ends_at
        if not ends_at:
            return 0
        seconds_left = (ends_at - timezone.now()).total_seconds()
        if seconds_left <= 0:
            return 0
        return _ceil(seconds_left / 86400)

    @property
    def is_trial_expired(self) -> bool:
        """True only when plan is trial AND the trial window has passed."""
        if not self.is_trial_plan:
            return False
        ends_at = self.trial_ends_at
        if not ends_at:
            return False
        return timezone.now() >= ends_at

    @property
    def show_trial_banner(self) -> bool:
        """Show the trial countdown/expired banner only in the final N days (see TRIAL_BANNER_THRESHOLD_DAYS)."""
        return self.is_trial_plan and self.trial_days_left <= TRIAL_BANNER_THRESHOLD_DAYS

    def save(self, *args, **kwargs):
        # Auto-start the trial clock the first time a school is saved as plan="trial".
        # Never overrides an explicitly set trial_started_at.
        if self.plan == ff.PLAN_TRIAL and self.trial_started_at is None:
            self.trial_started_at = timezone.now()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "School"
        verbose_name_plural = "Schools"

    def __str__(self) -> str:
        return self.display_name or self.slug

    @property
    def has_active_stripe_subscription(self) -> bool:
        """True when a Stripe customer + subscription exist and status is active-like."""
        return bool(
            self.stripe_customer_id
            and self.stripe_subscription_id
            and self.stripe_subscription_status in ("active", "trialing", "past_due")
        )


class SchoolAdminMembership(models.Model):
    class Role(models.TextChoices):
        OWNER  = "owner",  "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    # Rank used for >= comparisons; higher = more permissions.
    ROLE_RANK: dict[str, int] = {"owner": 3, "editor": 2, "viewer": 1}

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="school_memberships")
    school     = models.ForeignKey(School, on_delete=models.CASCADE, related_name="admin_memberships")
    role       = models.CharField(max_length=10, choices=Role.choices, default=Role.OWNER)
    is_active  = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_memberships",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "user"],
                name="unique_school_admin_membership",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} -> {self.school.slug} ({self.role})"

    def has_role(self, minimum_role: str) -> bool:
        return self.ROLE_RANK.get(self.role, 0) >= self.ROLE_RANK.get(minimum_role, 0)


class SchoolProgram(models.Model):
    """
    Authoritative DB record for a school's program/class offering.
    When School.program_field_key is set, the form renderer replaces YAML options
    for that field with active SchoolProgram records for this school.
    """
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="programs")
    name = models.CharField(max_length=255)
    # code matches Submission.data[program_field_key] — locked once any submission uses it
    code = models.CharField(max_length=120, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    display_order = models.PositiveIntegerField(default=0)
    capacity = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    auto_enroll = models.BooleanField(default=False)  # master switch for auto-enrollment
    waitlist_enabled = models.BooleanField(default=False)  # only meaningful when auto_enroll=True
    # [] = available on all forms; ["enrollment", "summer"] = specific forms only.
    # Exposed in Django admin only for v1 (not in school admin UI).
    form_keys = models.JSONField(default=list, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("school", "code")]
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return f"{self.school.slug} / {self.name} ({self.code})"

    def has_submissions(self) -> bool:
        return self.submissions.exists()

    def has_active_sessions(self) -> bool:
        return self.sessions.filter(is_active=True, is_deleted=False).exists()


class SchoolSession(models.Model):
    """
    Optional sub-grouping of a SchoolProgram (e.g. "Fall 2025", "Tuesdays 5 PM").
    When a program has active sessions, the public form shows sessions instead of
    the bare program.  Capacity / auto-enroll / waitlist are overrideable per session.
    """
    program          = models.ForeignKey(SchoolProgram, on_delete=models.CASCADE, related_name="sessions")
    name             = models.CharField(max_length=200)
    # Slugified from name on create; locked once any submission references this session.
    code             = models.CharField(max_length=64, blank=True, db_index=True)
    start_date       = models.DateField(null=True, blank=True)
    end_date         = models.DateField(null=True, blank=True)
    capacity         = models.PositiveIntegerField(null=True, blank=True)
    auto_enroll      = models.BooleanField(default=False)
    waitlist_enabled = models.BooleanField(default=False)
    is_active        = models.BooleanField(default=True, db_index=True)
    is_deleted       = models.BooleanField(default=False, db_index=True)
    display_order    = models.PositiveIntegerField(default=0)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("program", "code")]
        ordering = ["display_order", "name"]

    def __str__(self) -> str:
        return f"{self.program.school.slug} / {self.program.name} / {self.name}"

    def has_submissions(self) -> bool:
        return self.submissions.exists()


class SchoolEmailTemplate(models.Model):
    """
    Reusable email templates scoped to a school.
    Body is HTML from the contenteditable editor (bold/italic/underline only).
    Token syntax: {{full_name}}, {{first_name}}, {{email}}, {{program}},
                  {{status}}, {{school_name}}
    """
    school     = models.ForeignKey(School, on_delete=models.CASCADE, related_name="email_templates")
    name       = models.CharField(max_length=120)
    subject    = models.CharField(max_length=255)
    body       = models.TextField()
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.school.slug} / {self.name}"


class SchoolCustomToken(models.Model):
    """School-defined placeholder tokens for email templates (e.g. teacher, price, day)."""
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="custom_tokens")
    key    = models.CharField(max_length=50)   # used as {{key}} in templates
    label  = models.CharField(max_length=120)  # display name shown in editor panel

    class Meta:
        unique_together = [("school", "key")]
        ordering = ["key"]

    def __str__(self) -> str:
        return f"{self.school.slug} / {self.key}"


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

    ai_summary = models.JSONField(
        null=True,
        blank=True,
        help_text="Claude-generated summary (dict with 'summary' and 'criteria_scores').",
    )
    ai_summary_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the AI summary was last generated.",
    )

    # Internal staff notes — never shown to applicants.
    internal_notes = models.TextField(blank=True, default="")

    # Follow-up scheduling (Phase 11)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Per-school sequential application number.
    # Assigned on first save via select_for_update(School) to serialize concurrent creates.
    # null=True for existing rows; backfilled by data migration 0022.
    # No db_index=True — the unique_together constraint below already creates an index.
    school_submission_number = models.PositiveIntegerField(null=True, blank=True)

    # Application fee payment tracking (Phase 18).
    # payment_status choices: "" (not applicable), "pending", "paid", "waived", "failed"
    payment_intent_id = models.CharField(max_length=255, blank=True, default="")
    payment_status = models.CharField(max_length=20, blank=True, default="")

    # FK to SchoolProgram — set at submission time when school uses DB-driven programs.
    # Nullable: schools without program_field_key still work; existing rows are NULL until backfill.
    program = models.ForeignKey(
        "SchoolProgram",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submissions",
    )

    # FK to SchoolSession — set when the selected program option was a session-namespaced value.
    # NULL for schools without sessions, or submissions made before sessions were added.
    session = models.ForeignKey(
        "SchoolSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submissions",
    )

    # Family status page — token-based public URL, admin-authored notes visible to family.
    status_token = models.CharField(max_length=64, unique=True, default=generate_submission_status_token)
    public_notes = models.TextField(blank=True, default="")

    # Schedule change request — set when a family submits updated scheduling preferences.
    schedule_change_requested = models.BooleanField(default=False)
    schedule_change_requested_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("school", "school_submission_number")]

    def save(self, *args, **kwargs):
        # Invariant: a session submission must always reference the session's own program.
        if self.session_id is not None and self.program_id is None:
            self.program_id = self.session.program_id

        if self.pk is None and self.school_submission_number is None:
            with transaction.atomic():
                # Lock the school row to serialize concurrent submission creates for
                # the same school. Any second transaction blocks until this one commits.
                School.objects.select_for_update().get(pk=self.school_id)
                last = (
                    Submission.objects
                    .filter(school_id=self.school_id)
                    .aggregate(Max("school_submission_number"))
                    ["school_submission_number__max"]
                ) or 0
                self.school_submission_number = last + 1
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        num = self.school_submission_number
        return f"{self.school.slug} #{num}" if num else f"{self.school.slug} submission #{self.id}"
    
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

        # If the program FK is set, use it directly — most reliable for DB-backed programs.
        if self.program_id and self.program:
            return self.program.name

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
    

# ---------------------------------------------------------------------------
# DraftSubmission — save-and-resume magic link drafts
# ---------------------------------------------------------------------------

_DRAFT_EXPIRY_DAYS = 7


def _generate_draft_token() -> str:
    # token_urlsafe(32) produces ~43 chars; max_length=128 gives headroom for future rotation
    return secrets.token_urlsafe(32)


def _default_token_expires_at():
    return timezone.now() + timedelta(days=_DRAFT_EXPIRY_DAYS)


class DraftSubmission(models.Model):
    """
    Persists a partially-completed application so the applicant can resume
    via a magic-link email.  On final submit the draft is NOT deleted —
    submitted_at is set instead, allowing the link to render "already submitted."
    Expired/submitted drafts are candidates for periodic cleanup.

    Security note: the raw token is stored (not hashed).  This is an accepted
    MVP tradeoff — enrollment data is low-sensitivity and the token is
    short-lived (7 days).  Future: HMAC-SHA256 if sensitivity increases.
    """

    school = models.ForeignKey(
        "School", on_delete=models.CASCADE, related_name="draft_submissions"
    )
    # Mirrors Submission.form_key: "default" for single-form, "multi" for multi-form
    form_key = models.CharField(max_length=64, default="default")
    data = models.JSONField(default=dict)

    # token_urlsafe(32) ≈ 43 chars; 128 gives ample headroom for future rotation
    token = models.CharField(
        max_length=128, unique=True, db_index=True, default=_generate_draft_token
    )
    token_expires_at = models.DateTimeField(default=_default_token_expires_at)

    # Admin-initiated enrollment: link back to the Lead that started this draft.
    # Nullable — family-initiated drafts have no lead. SET_NULL so draft survives lead deletion.
    # Used for: (1) idempotent draft reuse, (2) bypassing save_resume_enabled in resume_draft_view.
    lead = models.ForeignKey(
        "Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="enrollment_drafts",
    )

    # Cached applicant email — used to send/resend the magic link
    email = models.CharField(max_length=254, blank=True, default="")
    # Multi-form only: last completed step key; blank = not past step 1
    last_form_key = models.CharField(max_length=64, blank=True, default="")
    # Throttle: don't resend email more than once per cooldown window
    last_email_sent_at = models.DateTimeField(null=True, blank=True)
    # Set on successful final submit (draft kept so link shows "already submitted")
    submitted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["token_expires_at"]),
        ]

    def is_expired(self) -> bool:
        return timezone.now() > self.token_expires_at

    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    def extend_expiry(self) -> None:
        """Reset the 7-day expiry window. Call before each save."""
        self.token_expires_at = timezone.now() + timedelta(days=_DRAFT_EXPIRY_DAYS)

    def __str__(self) -> str:
        return f"Draft {self.school.slug} / {self.email or 'no-email'} ({self.form_key})"


# ---------------------------------------------------------------------------
# Lead — pre-application interest capture
# ---------------------------------------------------------------------------

LEAD_STATUS_NEW = "new"
LEAD_STATUS_CONTACTED = "contacted"
LEAD_STATUS_TRIAL_SCHEDULED = "trial_scheduled"
LEAD_STATUS_TRIAL_COMPLETED = "trial_completed"
LEAD_STATUS_ENROLLED = "enrolled"
LEAD_STATUS_LOST = "lost"

LEAD_STATUS_CHOICES = [
    (LEAD_STATUS_NEW, "New"),
    (LEAD_STATUS_CONTACTED, "Contacted"),
    (LEAD_STATUS_TRIAL_SCHEDULED, "Trial Scheduled"),
    (LEAD_STATUS_TRIAL_COMPLETED, "Trial Completed"),
    (LEAD_STATUS_ENROLLED, "Enrolled"),
    (LEAD_STATUS_LOST, "Lost"),
]

LEAD_SOURCE_CHOICES = [
    ("website", "Website"),
    ("referral", "Referral"),
    ("social", "Social Media"),
    ("walk_in", "Walk-in"),
    ("phone", "Phone"),
    ("event", "Event"),
    ("other", "Other"),
    ("manual", "Manual Entry"),
    ("website_lead_form", "Lead Form"),   # public /lead/ form
    ("webhook", "Webhook"),               # external webhook intake
]


class Lead(models.Model):
    """
    Pre-application interest record. One Lead per school+email pair.

    Deduplication key: school + normalized_email (full stop, no time window).
    Tradeoff: a parent using one email for two children at the same school
    appears as a single lead. Acceptable for now; a household model can
    address this in a future iteration if needed.
    """

    # Identity
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="leads")
    public_id = models.CharField(
        max_length=16,
        unique=True,
        editable=False,
        db_index=True,
        blank=True,
    )
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, default="")

    # Normalized for dedup + search
    normalized_email = models.EmailField(db_index=True)
    normalized_phone = models.CharField(max_length=50, blank=True, default="", db_index=True)

    # Interest
    interested_in_label = models.CharField(max_length=200, blank=True, default="")
    interested_in_value = models.CharField(max_length=200, blank=True, default="")

    # Attribution
    source = models.CharField(
        max_length=50,
        choices=LEAD_SOURCE_CHOICES,
        blank=True,
        default="website",
    )
    utm_source = models.CharField(max_length=100, blank=True, default="")
    utm_medium = models.CharField(max_length=100, blank=True, default="")
    utm_campaign = models.CharField(max_length=100, blank=True, default="")

    # Pipeline
    status = models.CharField(
        max_length=40,
        choices=LEAD_STATUS_CHOICES,
        default=LEAD_STATUS_NEW,
        db_index=True,
    )
    notes = models.TextField(blank=True, default="")
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=255, blank=True, default="")

    # Which named lead form produced this record ("" = legacy /lead/ route)
    form_key = models.CharField(max_length=100, blank=True, default="", db_index=True)

    # Extra data: message, student_name, src param, webhook extras
    data = models.JSONField(default=dict, blank=True)

    # Conversion (Feature 6 will populate these)
    converted_submission = models.ForeignKey(
        "Submission",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="converted_leads",
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = []
        indexes = [
            models.Index(fields=["school", "status"]),
            models.Index(fields=["school", "normalized_email"]),
            models.Index(fields=["school", "created_at"]),
            models.Index(fields=["school", "next_follow_up_at"]),
            models.Index(fields=["school", "status", "next_follow_up_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id()
        self.normalized_email = (self.email or "").lower().strip()
        self.normalized_phone = re.sub(r"\D", "", self.phone or "")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


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


DEMO_TOKEN_DAYS = 14


class DemoAccessToken(models.Model):
    """
    Magic-link token for one-click login.
    purpose="demo": prospect demo access (uses demo domain, shows demo banner).
    purpose="onboarding": real admin login after conversion (uses app domain, no banner).
    """

    PURPOSE_DEMO = "demo"
    PURPOSE_ONBOARDING = "onboarding"
    PURPOSE_CHOICES = [(PURPOSE_DEMO, "Demo"), (PURPOSE_ONBOARDING, "Onboarding")]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="demo_tokens")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    pages_visited = models.JSONField(default=list, blank=True)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_DEMO)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DemoToken({self.school.slug}, {self.purpose}, expires {self.expires_at.date()})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def days_remaining(self) -> int:
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)


class OnboardingChecklistItem(models.Model):
    ITEMS = [
        ("school_created", "School created"),
        ("plan_configured", "Plan configured"),
        ("trial_configured", "Trial configured"),
        ("admin_invited", "Admin user invited"),
        ("team_access_configured", "Team access configured"),
        ("branding_configured", "Branding configured"),
        ("programs_configured", "Programs configured"),
        ("workflows_configured", "Enrollment workflows configured"),
        ("payment_configured", "Payment workflow configured"),
        ("email_templates_reviewed", "Email templates reviewed"),
        ("lead_capture_configured", "Lead capture configured"),
        ("website_integration_complete", "Website integration complete"),
        ("test_submission_completed", "Test submission completed"),
        ("email_delivery_verified", "Email delivery verified"),
        ("reports_verified", "Reports verified"),
        ("school_marked_live", "School marked Live"),
    ]
    ITEM_LABELS = dict(ITEMS)

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="onboarding_items")
    item = models.CharField(max_length=50, choices=ITEMS)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["school", "item"], name="unique_school_onboarding_item")
        ]

    def __str__(self) -> str:
        return f"{self.school.slug} — {self.item}"


class DemoArchive(models.Model):
    """Lightweight snapshot of demo data taken before conversion (rollback insurance)."""

    school = models.OneToOneField(School, on_delete=models.CASCADE, related_name="demo_archive")
    archived_at = models.DateTimeField(auto_now_add=True)
    archived_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    submissions_json = models.JSONField(default=list)
    leads_json = models.JSONField(default=list)
    config_yaml = models.TextField(blank=True)

    class Meta:
        verbose_name = "Demo Archive"

    def __str__(self) -> str:
        return f"DemoArchive({self.school.slug}, {self.archived_at.date()})"


class AdminPreference(models.Model):
    """Per-user admin UI preferences (theme, etc.).

    OneToOneField on User so it works for both school admins and superusers.
    Adding a new preference field later = one migration + one line here.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="admin_preference",
    )
    theme = models.CharField(
        max_length=32,
        choices=THEME_CHOICES,
        default=DEFAULT_THEME_KEY,
        db_index=True,
    )

    class Meta:
        verbose_name = "Admin Preference"
        verbose_name_plural = "Admin Preferences"

    def __str__(self) -> str:
        return f"{self.user.username} → {self.theme}"