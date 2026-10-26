from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from core.services.portal_environment import scope_schools
from . import guest_lists, workshop_followup
from .inventory_forms import party_catalog_data
from .models import SchedulingProfile


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="30/m", block=True)
def selections(request, school_slug, token):
    item = workshop_followup.resolve(school_slug, token)
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=item.profile_id)
    people = list(item.workshop_request.participants.all())
    selected_id = request.POST.get("participant") if request.method == "POST" else request.GET.get("participant")
    person = next((p for p in people if str(p.pk) == selected_id), None) if selected_id else next((p for p in people if p.selections_deferred), None)
    if selected_id and person is None:
        from django.http import Http404
        raise Http404
    editable = guest_lists.editable(item)
    form = workshop_followup.form_for(item, person, request.POST if request.method == "POST" else None) if person else None
    error = ""
    if request.method == "POST":
        if person is None:
            error = "Choose a participant."
        else:
            try:
                item, form = workshop_followup.save(school_slug, token, person.pk, request.POST)
                if not form.errors:
                    return redirect(request.path + "?saved=1")
            except ValidationError as exc:
                error = " ".join(exc.messages)
    response = render(request, "scheduling/workshop_followup.html", {
        "business": item.profile.school, "item": item, "people": people, "person": person,
        "form": form, "editable": editable, "error": error, "saved": request.GET.get("saved") == "1",
        "party_catalog": party_catalog_data(item.workshop_request.session.offering),
    }, status=400 if error or form and form.errors else 200)
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
