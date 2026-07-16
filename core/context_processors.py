from core.models import SchoolAdminMembership


def school_admin_membership(request):
    """
    Injects `user_school_memberships` (all active memberships) and
    `user_school_membership` (first active membership, for single-school users)
    into every template context.

    With role-based access a user may belong to multiple schools.
    Views that need the current-school membership should inject `current_membership`
    themselves via _school_admin_base_context; this processor covers nav/admin chrome.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"user_school_membership": None, "user_school_memberships": []}

    memberships = list(
        SchoolAdminMembership.objects
        .filter(user=request.user, is_active=True)
        .select_related("school")
    )
    first = memberships[0] if memberships else None
    return {
        "user_school_membership": first,
        "user_school_memberships": memberships,
    }
