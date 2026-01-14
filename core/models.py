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

    def __str__(self) -> str:
        return self.display_name or self.slug


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
    