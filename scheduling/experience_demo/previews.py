"""Same-origin integration wrappers around the actual, explicitly enabled demo."""
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .config import context


@never_cache
@require_GET
def preview(request, variant="index"):
    if variant not in {"index", "inline", "modal"}:
        raise Http404
    context(request, "plushumi-demo")
    response = render(request, f"scheduling/experience/integrations/{variant}.html")
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Content-Security-Policy"] = "frame-ancestors 'none'; frame-src 'self'; object-src 'none'; base-uri 'none'"
    return response
