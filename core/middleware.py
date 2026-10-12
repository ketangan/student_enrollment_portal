from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.urls import reverse


class PortalEnvironmentMiddleware:
    """Keep tenant pages on their configured host without redirecting writes."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from core.services.portal_environment import current_environment, request_environment
        token = current_environment.set(request_environment(request) or "unknown")
        try:
            return self.get_response(request)
        finally:
            current_environment.reset(token)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from core.models import School
        from core.services.portal_environment import request_environment, school_environment
        environment = request_environment(request)
        if environment == "production" and request.path.startswith("/demo/"):
            from core.services.url_builder import demo_url
            from django.http import HttpResponseForbidden
            if request.method not in {"GET", "HEAD"}:
                return HttpResponseForbidden("Open the demo portal before making changes.")
            return redirect(demo_url(request.get_full_path()))
        slug = view_kwargs.get("school_slug")
        if request.path.startswith("/ops/schools/"):
            slug = view_kwargs.get("slug")
        if not environment or not slug or not request.path.startswith(("/schools/", "/bookings/", "/ops/schools/")):
            return None  # Provider callbacks retain their configured endpoint and signature checks.
        school = School.objects.filter(slug=slug).first()
        if school is None or school_environment(school) == environment:
            return None
        if request.method not in {"GET", "HEAD"}:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Open this workspace on its demo or production portal before making changes.")
        from core.services.url_builder import app_url, demo_url
        return redirect((demo_url if school.is_demo else app_url)(request.get_full_path()))


class SchoolAdminRedirectMiddleware:
    """
    Redirects non-superuser school admins away from the Django admin index
    to their modern school dashboard (/schools/<slug>/admin/).

    Assumptions:
    - School admins are Django staff users (is_staff=True) with a
      SchoolAdminMembership.  Superusers are never redirected.
    - The admin index path is derived from ``settings.ADMIN_URL`` (defaults to
      ``"admin/"``).  Change ADMIN_URL in settings to harden a deployment; the
      middleware automatically follows without code changes.

    Scope: only intercepts GET requests to the admin index root.  Deep-links to
    specific admin pages (e.g. /admin/core/submission/42/change/) are left
    untouched so school admins can still reach submission/lead detail pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        admin_prefix = getattr(settings, "ADMIN_URL", "admin/").strip("/")
        # Both slash and no-slash variants to handle APPEND_SLASH variation.
        self._admin_index_paths = frozenset([
            f"/{admin_prefix}/",
            f"/{admin_prefix}",
        ])

    def __call__(self, request):
        if (
            request.method == "GET"
            and request.path in self._admin_index_paths
            and request.user.is_authenticated
            and request.user.is_staff
            and not request.user.is_superuser
        ):
            from core.models import SchoolAdminMembership
            membership = SchoolAdminMembership.objects.filter(
                user=request.user, is_active=True
            ).select_related("school").first()
            if membership:
                return redirect(
                    reverse(
                        "school_dashboard",
                        kwargs={"school_slug": membership.school.slug},
                    )
                )

        return self.get_response(request)

    def process_exception(self, request, exception):
        from core.views_school_common import TrialExpiredError
        if isinstance(exception, TrialExpiredError):
            from django.contrib import messages
            # Payment expiry already has a persistent banner on the billing page.
            if exception.reason != "payment":
                messages.error(
                    request,
                    "Your free trial has expired. Please upgrade your plan to continue.",
                )
            return redirect(
                reverse("school_billing", kwargs={"school_slug": exception.school_slug})
            )

    def process_view(self, request, view_func, view_args, view_kwargs):
        # Apply the same school suspension to public enrollment/status links.
        # Provider webhooks must keep working to settle existing payments.
        slug = view_kwargs.get("school_slug")
        if not slug or not request.path.startswith(("/schools/", "/bookings/")):
            return None
        if "/admin/" in request.path or getattr(request.user, "is_superuser", False):
            return None
        from core.models import School
        school = School.objects.filter(slug=slug).first()
        if school and school.is_payment_access_expired:
            from core.services.billing_access import billing_role
            if request.path.startswith("/bookings/") and billing_role(request.user, school):
                return redirect("school_billing", school_slug=school.slug)
            from django.shortcuts import render
            return render(request, "school_unavailable.html", {"school": school}, status=403)
