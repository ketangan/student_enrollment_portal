from django.http import Http404
from django.shortcuts import render, redirect
from django.urls import reverse

from .models import School, Submission
from .services.config_loader import load_school_config
from .services.validation import validate_submission


def apply_view(request, school_slug: str):
    """
    YAML-driven multi-tenant form:
    - GET: render form
    - POST: validate + store JSONB submission
    """
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    # Ensure School exists in DB (keeps admin list consistent)
    school, _created = School.objects.get_or_create(
        slug=school_slug,
        defaults={
            "display_name": config.display_name,
            "website_url": config.raw.get("school", {}).get("website_url", ""),
            "source_url": config.raw.get("school", {}).get("source_url", ""),
            "logo_url": config.branding.get("logo_url", ""),
            "theme_primary_color": config.branding["theme"]["primary_color"],
            "theme_accent_color": config.branding["theme"]["accent_color"],
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
                    "branding": config.branding,
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
            "branding": config.branding,
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
