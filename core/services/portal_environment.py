"""Demo and production share storage, but not their portal views."""
from contextvars import ContextVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q


current_environment = ContextVar("portal_environment", default="unknown")


def request_environment(request):
    production = urlsplit(settings.APP_BASE_URL).hostname
    demo = urlsplit(settings.DEMO_BASE_URL).hostname
    if not production or not demo or production == demo:
        return None  # Local development can serve both kinds of business.
    host = urlsplit("//" + request.get_host()).hostname
    return {production: "production", demo: "demo"}.get(host)


def school_environment(school):
    return "demo" if school.is_demo else "production"


def scope_schools(queryset, request, prefix=""):
    environment = request_environment(request)
    if environment:
        return queryset.filter(**{prefix + "is_demo": environment == "demo"})
    return queryset


def scope_audit(queryset, request):
    environment = request_environment(request)
    return queryset.filter(environment=environment) if environment else queryset


def scope_users(queryset, request):
    environment = request_environment(request)
    if not environment:
        return queryset
    demo = environment == "demo"
    return queryset.filter(Q(is_superuser=True)
        | Q(school_memberships__school__is_demo=demo)
        | Q(scheduling_memberships__profile__school__is_demo=demo)
        | Q(school_memberships__isnull=True, scheduling_memberships__isnull=True)).distinct()


def scope_incidents(queryset, request):
    environment = request_environment(request)
    if not environment:
        return queryset
    from django.contrib.auth import get_user_model
    return queryset.filter(Q(affected_school__is_demo=environment == "demo")
        | Q(affected_school__isnull=True, affected_user__in=scope_users(get_user_model().objects.all(), request))
        | Q(affected_school__isnull=True, affected_user__isnull=True))


def scope_form_choices(form, request):
    """Apply the same boundary to dropdowns and submitted foreign keys."""
    from core.models import School
    from django.contrib.auth import get_user_model
    from scheduling.models import SchedulingProfile
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        if queryset.model == School:
            field.queryset = scope_schools(queryset, request)
        elif queryset.model == get_user_model():
            field.queryset = scope_users(queryset, request)
        elif queryset.model == SchedulingProfile:
            field.queryset = scope_schools(queryset, request, "school__")
    return form
