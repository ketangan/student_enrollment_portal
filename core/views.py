from django.http import Http404
from django.shortcuts import render, redirect
from django.urls import reverse

from .models import School, Submission
from .services.config_loader import load_school_config
from .services.validation import validate_submission


# Phase 9: default branding (used when YAML has missing branding keys)
DEFAULT_BRANDING = {
    "logo_url": None,
    "theme": {
        # Keep compatibility with your existing DB fields and UI
        "primary_color": "#111827",
        "accent_color": "#ea580c",
        # Optional extras (useful later for CSS variables)
        "background": "#f7f7fb",
        "card": "#ffffff",
        "text": "#111827",
        "muted": "#6b7280",
        "border": "#e5e7eb",
        "radius": "16px",
    },
    # Optional per-school assets (Phase 9 option 2)
    "custom_css": None,
    "custom_js": None,
}


def merge_branding(branding_in: dict | None) -> dict:
    """
    Merge school-provided branding over DEFAULT_BRANDING.
    - No if/else explosion
    - Missing keys are filled with defaults
    - Supports optional custom_css/custom_js
    """
    branding_in = branding_in or {}

    merged = {
        "logo_url": branding_in.get("logo_url", DEFAULT_BRANDING["logo_url"]),
        "custom_css": branding_in.get("custom_css", DEFAULT_BRANDING["custom_css"]),
        "custom_js": branding_in.get("custom_js", DEFAULT_BRANDING["custom_js"]),
        "theme": DEFAULT_BRANDING["theme"].copy(),
    }

    # Merge theme keys
    theme_in = branding_in.get("theme") or {}
    merged["theme"].update(theme_in)

    # Safety: if someone only sets one of these, still keep usable colors
    if not merged["theme"].get("accent_color"):
        merged["theme"]["accent_color"] = DEFAULT_BRANDING["theme"]["accent_color"]
    if not merged["theme"].get("primary_color"):
        merged["theme"]["primary_color"] = merged["theme"]["text"] or DEFAULT_BRANDING["theme"]["text"]

    return merged


def apply_view(request, school_slug: str):
    """
    YAML-driven multi-tenant form:
    - GET: render form
    - POST: validate + store JSONB submission
    """
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    # Phase 9: ensure branding always has defaults + optional custom_css/custom_js
    branding = merge_branding(getattr(config, "branding", None))

    # Ensure School exists in DB (keeps admin list consistent)
    school, _created = School.objects.get_or_create(
        slug=school_slug,
        defaults={
            "display_name": config.display_name,
            "website_url": config.raw.get("school", {}).get("website_url", ""),
            "source_url": config.raw.get("school", {}).get("source_url", ""),
            "logo_url": branding.get("logo_url") or "",
            "theme_primary_color": branding["theme"].get("primary_color") or "",
            "theme_accent_color": branding["theme"].get("accent_color") or "",
        },
    )

    if request.method == "POST":
        cleaned, errors = validate_submission(config.form, request.POST)
        if errors:
            return render(
                request,
                "apply_form.html",
                {
                    "school": school,
                    "branding": branding,  # use merged branding
                    "form": config.form,
                    "errors": errors,
                    "values": request.POST,
                },
            )

        Submission.objects.create(school=school, data=cleaned)
        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    return render(
        request,
        "apply_form.html",
        {
            "school": school,
            "branding": branding,  # use merged branding
            "form": config.form,
            "errors": {},
            "values": {},
        },
    )


def apply_success_view(request, school_slug: str):
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    return render(
        request,
        "apply_success.html",
        {
            "school_slug": school_slug,
            "school_name": config.display_name,
        },
    )
