from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.urls import reverse


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
            try:
                membership = request.user.school_membership
            except ObjectDoesNotExist:
                membership = None
            if membership:
                return redirect(
                    reverse(
                        "school_dashboard",
                        kwargs={"school_slug": membership.school.slug},
                    )
                )

        return self.get_response(request)
