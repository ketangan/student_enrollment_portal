"""Booking-page and popup options for the explicitly enabled customer demo."""
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .config import context


@never_cache
@require_GET
def preview(request, variant="index"):
    if variant not in {"index", "inline", "modal"}:
        raise Http404
    context(request, "plushumi-demo")
    if variant == "inline":
        # Keep previously shared links working without nesting a complete booking page.
        response = redirect("scheduling:experience_start", school_slug="plushumi-demo")
    else:
        response = render(request, f"scheduling/experience/integrations/{variant}.html")
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Content-Security-Policy"] = "frame-ancestors 'none'; frame-src 'self'; object-src 'none'; base-uri 'none'"
    return response
