from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from .integrations import square_connection as service
from .models import SquareConnection


@never_cache
@login_required
@require_POST
def update(request, school_slug):
    profile, _ = service.access(request, school_slug)
    action = request.POST.get("action")
    if action not in {"connect", "check", "disconnect", "locations", "map"}:
        return HttpResponseBadRequest("Choose a valid Square action.")
    try:
        if action == "connect":
            return redirect(service.begin(request, school_slug))
        revision = request.POST.get("revision", "")
        if not revision.isdecimal() or len(revision) > 10:
            raise ValidationError("Reload Square settings before trying again.")
        connection = get_object_or_404(SquareConnection, profile=profile, environment=service.environment(profile))
        if action in {"locations", "map"}:
            service.update_locations(request, school_slug, int(revision), mappings=request.POST if action == "map" else None)
            messages.success(request, "Location mappings saved." if action == "map" else "Square locations refreshed.")
            return redirect("scheduling:integrations", school_slug=school_slug)
        service.maintain(connection.pk, disconnect=action == "disconnect", actor=request.user, expected_revision=int(revision))
        messages.success(request, "Square disconnected." if action == "disconnect" else "Square connection verified.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("scheduling:integrations", school_slug=school_slug)


@sensitive_variables()
@never_cache
@require_GET
def callback(request):
    try:
        slug, connected = service.finish(request)
    except (ValidationError, Http404):
        response = HttpResponseBadRequest("Square connection was not completed. Return to Settings > Integrations and try again.")
        response["Referrer-Policy"] = "no-referrer"
        return response
    messages.success(request, "Square account connected." if connected else "Square connection canceled. No settings changed.")
    response = redirect("scheduling:integrations", school_slug=slug)
    response["Referrer-Policy"] = "no-referrer"
    return response
