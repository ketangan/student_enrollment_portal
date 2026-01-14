from django.http import Http404, HttpResponse
from .models import School


def apply_view(request, school_slug: str):
    """
    Temporary placeholder view to prove routing + tenant lookup works.
    Phase 6 will replace this with YAML-driven dynamic form rendering.
    """
    try:
        school = School.objects.get(slug=school_slug)
    except School.DoesNotExist:
        raise Http404("School not found")

    return HttpResponse(
        f"<h1>Apply: {school.display_name or school.slug}</h1>"
        f"<p>Slug: {school.slug}</p>"
        f"<p>This is a placeholder. Next step: YAML-driven form.</p>"
    )
