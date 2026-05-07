from core.models import SchoolAdminMembership


def school_admin_membership(request):
    """
    Injects `user_school_membership` into every template context.

    Returns None when the user is anonymous, unauthenticated, or has no
    SchoolAdminMembership row — never raises.  Admin templates should use
    this variable instead of directly accessing request.user.school_membership,
    which raises RelatedObjectDoesNotExist for staff without a membership row.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"user_school_membership": None}
    try:
        membership = request.user.school_membership
    except SchoolAdminMembership.DoesNotExist:
        membership = None
    return {"user_school_membership": membership}
