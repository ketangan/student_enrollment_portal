# core/services/school_access.py
from django.http import Http404
from django.template.response import TemplateResponse

def is_school_active(school) -> bool:
    """Return True if the school is active (not locked)."""
    return getattr(school, "is_active", True)

def require_school_active(request, school):
    """Raise 404 or return a lockout page if the school is inactive."""
    if not is_school_active(school):
        # Optionally render a lockout template, or just raise 404
        return TemplateResponse(request, "school_locked.html", {"school": school}, status=403)
    return None
