from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


def _post_login_redirect(request, next_url=""):
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    if request.user.is_superuser:
        return redirect("ops_dashboard")
    membership = getattr(request.user, "school_membership", None)
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
