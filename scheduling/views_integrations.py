import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods

from .integrations import service
from .integrations import square_connection
from .integrations.payloads import DESTINATIONS, example_booking
from .integrations.registry import PROVIDERS, provider
from .views import _context


@never_cache
@login_required
@require_GET
def index(request, school_slug):
    profile, role = square_connection.access(request, school_slug)
    context = _context(request, profile, role, "integrations")
    context.update(connections=service.connections(profile), providers=PROVIDERS.values())
    context["square"] = square_connection.context(request, profile)
    return render(request, "scheduling/integrations/index.html", context)


@sensitive_post_parameters("credential")
@sensitive_variables()
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, school_slug, provider_key=None, pk=None):
    profile, role = service.access(request.user, school_slug)
    form = (service.save_connection(request.user, school_slug, request.POST, provider_key, pk)
        if request.method == "POST" else service.form_for(profile, provider_key, pk))
    if request.method == "POST" and form.is_valid():
        messages.success(request, "Connection settings saved. Nothing has been connected or sent.")
        return redirect("scheduling:integration_edit", school_slug=school_slug, pk=form.instance.pk)
    context = _context(request, profile, role, "integrations")
    context.update(form=form, provider=form.spec, connection=form.instance, editing=bool(pk))
    return render(request, "scheduling/integrations/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_GET
def preview(request, school_slug, pk):
    profile, role = service.access(request.user, school_slug)
    connection = get_object_or_404(service.connections(profile), pk=pk)
    adapter = DESTINATIONS.get(connection.provider)
    if adapter is None:
        raise Http404("No destination preview for this provider")
    context = _context(request, profile, role, "integrations")
    context.update(connection=connection, provider=provider(connection.provider))
    try:
        projection = adapter.project(example_booking(), connection.configuration, connection.field_mapping)
        context.update(payload=json.dumps(projection.payload, indent=2), preview_rows=projection.rows,
            api_version=projection.api_version)
    except (ValidationError, KeyError):
        context["preview_error"] = "These saved settings cannot be previewed. Check the destination and field mappings."
    return render(request, "scheduling/integrations/preview.html", context)
