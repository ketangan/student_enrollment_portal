from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

DEMO_SESSION_TOKEN_KEY = "demo_token_id"
DEMO_SESSION_PAGES_KEY = "demo_visited_pages"


def _post_login_redirect(request, next_url=""):
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    if request.user.is_superuser:
        return redirect("ops_dashboard")
    from core.models import SchoolAdminMembership
    membership = SchoolAdminMembership.objects.filter(
        user=request.user, is_active=True
    ).select_related("school").first()
    if membership:
        return redirect("school_dashboard", school_slug=membership.school.slug)
    return redirect("/")


def login_view(request):
    if request.user.is_authenticated:
        return _post_login_redirect(request)

    next_url = request.GET.get("next", "") or request.POST.get("next", "")
    error = None

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return _post_login_redirect(request, next_url)
        error = "Invalid username or password."
    else:
        form = AuthenticationForm()

    return render(request, "login.html", {"form": form, "error": error, "next": next_url})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def demo_access_view(request, token):
    """Magic-link handler: validate token, log in as school's demo admin, redirect to dashboard."""
    from core.models import DemoAccessToken, AdminAuditLog

    try:
        demo_token = DemoAccessToken.objects.select_related("school").get(token=token)
    except DemoAccessToken.DoesNotExist:
        return render(request, "demo_expired.html", {"reason": "not_found"}, status=404)

    # Old demo links to a converted school → redirect to their live enrollment form.
    # Onboarding tokens always log in normally regardless of is_demo.
    if not demo_token.school.is_demo and demo_token.purpose == demo_token.PURPOSE_DEMO:
        return redirect("apply", school_slug=demo_token.school.slug)

    if demo_token.is_expired:
        return render(request, "demo_expired.html", {
            "reason": "expired",
            "school": demo_token.school,
        })

    membership = demo_token.school.admin_memberships.select_related("user").first()
    if not membership:
        return render(request, "demo_expired.html", {
            "reason": "no_user",
            "school": demo_token.school,
        })

    user = membership.user
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    # Only set demo session for demo-purpose tokens — onboarding tokens skip the demo banner.
    if demo_token.purpose == demo_token.PURPOSE_DEMO:
        request.session[DEMO_SESSION_TOKEN_KEY] = demo_token.pk
        request.session[DEMO_SESSION_PAGES_KEY] = []

    demo_token.last_used_at = timezone.now()
    demo_token.save(update_fields=["last_used_at"])

    AdminAuditLog.objects.create(
        actor=user,
        action="action",
        model_label="core.demoaccesstoken",
        object_id=str(demo_token.pk),
        object_repr=str(demo_token),
        extra={"name": "demo_access", "school": demo_token.school.slug},
    )

    return redirect("school_dashboard", school_slug=demo_token.school.slug)
