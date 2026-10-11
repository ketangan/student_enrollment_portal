from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .credit_forms import RedeemCreditForm, RevokeCreditForm
from .credits import (action_token, credit_query, get_credit, policy_form, policy_for,
                      redeem, redemption_plan, revoke, save_policy)
from .lessons import lesson_access, lesson_records
from .views import _context


@never_cache
@login_required
@require_GET
def credit_list(request, school_slug):
    profile, role = lesson_access(request.user, school_slug)
    items = credit_query(profile)
    family = request.GET.get("family", "")
    if family:
        household = get_object_or_404(lesson_records(profile, "households"), pk=family if family.isdigit() else 0)
        items = items.filter(source__series__student__household=household)
    context = _context(request, profile, role, "lessons")
    context.update(items=Paginator(items, 50).get_page(request.GET.get("page")), family=family,
                   households=lesson_records(profile, "households"), policy=policy_for(profile))
    return render(request, "scheduling/credits.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def policy(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    if request.method == "POST":
        form = save_policy(request.user, school_slug, request.POST)
        if form.is_valid():
            messages.success(request, "Policy saved. Existing credits keep their original rules and expiry.")
            return redirect("scheduling:lesson_credits", school_slug=school_slug)
    else:
        form = policy_form(profile)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title="Make-up policy", submit_label="Save policy")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_GET
def detail(request, school_slug, public_id):
    profile, role = lesson_access(request.user, school_slug)
    credit = get_credit(profile, public_id)
    context = _context(request, profile, role, "lessons")
    context.update(credit=credit, events=credit.events.select_related("actor", "occurrence__series"))
    with timezone.override(ZoneInfo(credit.snapshot["timezone"])):
        return render(request, "scheduling/credit_detail.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def book(request, school_slug, public_id):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    credit = get_credit(profile, public_id)
    form = RedeemCreditForm(request.POST if request.method == "POST" else None, occurrence=credit.source,
        initial={"teacher": credit.source.series.teacher_id, "location": credit.source.series.location_id,
                 "room": credit.source.series.room_id})
    context = _context(request, profile, role, "lessons")
    context.update(form=form, credit=credit)
    if request.method == "POST" and form.is_valid():
        try:
            if request.POST.get("action") == "confirm":
                series = redeem(request.user, school_slug, public_id, request.POST, request.POST.get("preview_token", ""))
                messages.success(request, "Make-up booked using one lesson credit. No payment or notification was sent.")
                return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=series.public_id)
            rows, revision = redemption_plan(credit, form.cleaned_data)
            context.update(rows=rows, token=action_token(request.user, credit, "redeem", request.POST, revision),
                can_confirm=rows[0]["state"] == "scheduled", zone=form.cleaned_data["location"].timezone_name)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(request, "scheduling/credit_book.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def revoke_credit(request, school_slug, public_id):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    credit = get_credit(profile, public_id)
    form = RevokeCreditForm(request.POST if request.method == "POST" else None,
        initial={"revision": action_token(request.user, credit, "revoke", {}, credit.version)})
    if request.method == "POST" and form.is_valid():
        try:
            revoke(request.user, school_slug, public_id, request.POST)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Lesson credit revoked. No money was refunded.")
            return redirect("scheduling:lesson_credit", school_slug=school_slug, public_id=public_id)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, credit=credit, title="Revoke lesson credit", submit_label="Revoke credit")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)
