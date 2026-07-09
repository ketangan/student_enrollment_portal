"""
Ops portal views — /ops/ prefix, superuser-only.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, FloatField, Max, Q, Value
from django.db.models.functions import Cast, NullIf
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import AdminAuditLog, DemoAccessToken, Lead, OnboardingChecklistItem, School, SchoolAdminMembership, Submission


def ops_required(view_func):
    """Decorator: must be authenticated superuser, else redirect to login."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.urls import reverse
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_superuser:
            raise PermissionError("Superuser access required.")
        return view_func(request, *args, **kwargs)
    return wrapper


def _log(request, action, model_label, object_id, object_repr, extra=None):
    AdminAuditLog.objects.create(
        actor=request.user,
        action=action,
        model_label=model_label,
        object_id=str(object_id),
        object_repr=object_repr,
        extra=extra or {},
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────

@ops_required
def ops_dashboard_view(request):
    now = timezone.now()
    seven_days = now + timezone.timedelta(days=7)

    total_schools = School.objects.count()
    active_schools = School.objects.filter(is_active=True).count()
    inactive_schools = total_schools - active_schools

    trial_schools = School.objects.filter(plan="trial", is_active=True, trial_started_at__isnull=False)
    expiring_soon = []
    expired = []
    for school in trial_schools:
        ends = school.trial_ends_at
        if ends is None:
            continue
        if ends <= now:
            expired.append(school)
        elif ends <= seven_days:
            expiring_soon.append(school)

    expiring_soon.sort(key=lambda s: s.trial_ends_at)
    expired.sort(key=lambda s: s.trial_ends_at, reverse=True)

    total_users = User.objects.filter(is_active=True).count()

    recent_schools = School.objects.order_by("-created_at")[:5]

    recent_activity = (
        AdminAuditLog.objects
        .select_related("actor")
        .order_by("-created_at")[:15]
    )

    return render(request, "ops/dashboard.html", {
        "active_nav": "dashboard",
        "total_schools": total_schools,
        "active_schools": active_schools,
        "inactive_schools": inactive_schools,
        "expiring_soon": expiring_soon,
        "expired_trials": expired,
        "total_users": total_users,
        "recent_schools": recent_schools,
        "recent_activity": recent_activity,
    })


# ── Schools list + create ─────────────────────────────────────────────────────

@ops_required
def ops_schools_list_view(request):
    from core.services.feature_flags import PLAN_CHOICES

    qs = School.objects.annotate(
        submission_count=Count("submissions", distinct=True),
        lead_count=Count("leads", distinct=True),
        member_count=Count("admin_memberships", distinct=True),
    ).order_by("-created_at")

    q = request.GET.get("q", "").strip()
    plan_filter = request.GET.get("plan", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if q:
        qs = qs.filter(Q(display_name__icontains=q) | Q(slug__icontains=q))
    if plan_filter:
        qs = qs.filter(plan=plan_filter)
    if status_filter == "active":
        qs = qs.filter(is_active=True)
    elif status_filter == "inactive":
        qs = qs.filter(is_active=False)

    return render(request, "ops/schools_list.html", {
        "active_nav": "schools",
        "schools": qs,
        "q": q,
        "plan_filter": plan_filter,
        "status_filter": status_filter,
        "plan_choices": PLAN_CHOICES,
    })


@ops_required
def ops_school_create_view(request):
    from core.forms_ops import OpsSchoolCreateForm

    if request.method == "POST":
        form = OpsSchoolCreateForm(request.POST)
        if form.is_valid():
            school = form.save()
            _log(request, "add", "core.school", school.pk, str(school),
                 {"name": "create_school", "slug": school.slug, "plan": school.plan})
            messages.success(request, f"School '{school.display_name or school.slug}' created.")
            return redirect("ops_school_detail", slug=school.slug)
    else:
        form = OpsSchoolCreateForm()

    return render(request, "ops/school_form.html", {
        "active_nav": "schools",
        "form": form,
        "form_title": "Create School",
        "submit_label": "Create",
    })


# ── School detail + edit ──────────────────────────────────────────────────────

@ops_required
def ops_school_detail_view(request, slug):
    from core.forms_ops import OpsSchoolEditForm

    school = get_object_or_404(School, slug=slug)

    if request.method == "POST":
        form = OpsSchoolEditForm(request.POST, instance=school)
        if form.is_valid():
            changed = {
                f: {"old": str(form.initial.get(f, "")), "new": str(form.cleaned_data[f])}
                for f in form.changed_data
            }
            form.save()
            _log(request, "change", "core.school", school.pk, str(school),
                 {"name": "edit_school", "changes": changed})
            messages.success(request, "School updated.")
            return redirect("ops_school_detail", slug=school.slug)
    else:
        form = OpsSchoolEditForm(instance=school)

    members = school.admin_memberships.select_related("user").all()
    recent_audit = AdminAuditLog.objects.filter(
        model_label="core.school", object_id=str(school.pk)
    ).order_by("-created_at")[:20]

    from core.models import Submission, Lead
    submission_count = Submission.objects.filter(school=school).count()
    lead_count = Lead.objects.filter(school=school).count()

    demo_token = DemoAccessToken.objects.filter(school=school).order_by("-created_at").first()
    from core.services.url_builder import demo_reverse, app_reverse
    demo_link = (
        demo_reverse("demo_access", kwargs={"token": demo_token.token})
        if demo_token and demo_token.purpose == DemoAccessToken.PURPOSE_DEMO else None
    )
    onboarding_link = (
        app_reverse("demo_access", kwargs={"token": demo_token.token})
        if demo_token and demo_token.purpose == DemoAccessToken.PURPOSE_ONBOARDING else None
    )

    from core.services.onboarding import get_or_create_checklist, qr_base64
    checklist_items = get_or_create_checklist(school)
    checklist_done = sum(1 for i in checklist_items if i.completed_at)
    checklist_total = len(checklist_items)

    enrollment_url = app_reverse("apply", kwargs={"school_slug": school.slug})
    iframe_snippet = (
        f'<iframe src="{enrollment_url}" width="100%" height="800" '
        f'frameborder="0" style="border:none;"></iframe>'
    )

    welcome_sent = AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(school.pk),
        extra__name="customer_welcome_email_sent",
    ).exists()

    return render(request, "ops/school_detail.html", {
        "active_nav": "schools",
        "school": school,
        "form": form,
        "members": members,
        "recent_audit": recent_audit,
        "submission_count": submission_count,
        "lead_count": lead_count,
        "demo_token": demo_token,
        "demo_link": demo_link,
        "onboarding_link": onboarding_link,
        "checklist_items": checklist_items,
        "checklist_done": checklist_done,
        "checklist_total": checklist_total,
        "enrollment_url": enrollment_url,
        "iframe_snippet": iframe_snippet,
        "welcome_sent": welcome_sent,
    })


# ── School memberships ────────────────────────────────────────────────────────

@ops_required
@require_POST
def ops_school_member_add_view(request, slug):
    school = get_object_or_404(School, slug=slug)
    email = request.POST.get("email", "").strip().lower()

    if not email:
        messages.error(request, "Email is required.")
        return redirect("ops_school_detail", slug=slug)

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        messages.error(request, f"No user found with email '{email}'. Create the user first.")
        return redirect("ops_school_detail", slug=slug)

    if hasattr(user, "school_membership"):
        existing = user.school_membership
        if existing.school_id == school.pk:
            messages.warning(request, f"{email} is already a member of this school.")
        else:
            messages.error(request, f"{email} already belongs to '{existing.school}'. Remove them first.")
        return redirect("ops_school_detail", slug=slug)

    SchoolAdminMembership.objects.create(user=user, school=school)
    _log(request, "add", "core.schooladminmembership", user.pk, f"{user.email} → {school.slug}",
         {"name": "add_member", "email": email, "school": slug})
    messages.success(request, f"{email} added as school admin.")
    return redirect("ops_school_detail", slug=slug)


@ops_required
@require_POST
def ops_school_member_remove_view(request, slug, user_id):
    school = get_object_or_404(School, slug=slug)
    membership = get_object_or_404(SchoolAdminMembership, school=school, user_id=user_id)
    email = membership.user.email
    membership.delete()
    _log(request, "delete", "core.schooladminmembership", user_id, f"{email} → {slug}",
         {"name": "remove_member", "email": email, "school": slug})
    messages.success(request, f"{email} removed.")
    return redirect("ops_school_detail", slug=slug)


# ── Users list + detail ───────────────────────────────────────────────────────

@ops_required
def ops_users_list_view(request):
    qs = User.objects.select_related("school_membership__school").order_by("-date_joined")

    q = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "").strip()
    status_filter = request.GET.get("status", "").strip()

    if q:
        qs = qs.filter(
            Q(email__icontains=q) | Q(username__icontains=q) |
            Q(first_name__icontains=q) | Q(last_name__icontains=q)
        )
    if role_filter == "superuser":
        qs = qs.filter(is_superuser=True)
    elif role_filter == "staff":
        qs = qs.filter(is_staff=True, is_superuser=False)
    elif role_filter == "school_admin":
        qs = qs.filter(school_membership__isnull=False)

    if status_filter == "active":
        qs = qs.filter(is_active=True)
    elif status_filter == "inactive":
        qs = qs.filter(is_active=False)

    return render(request, "ops/users_list.html", {
        "active_nav": "users",
        "users": qs,
        "q": q,
        "role_filter": role_filter,
        "status_filter": status_filter,
    })


@ops_required
def ops_user_detail_view(request, user_id):
    from core.forms_ops import OpsUserEditForm

    target_user = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        form = OpsUserEditForm(request.POST, instance=target_user)
        if form.is_valid():
            changed = {
                f: {"old": str(form.initial.get(f, "")), "new": str(form.cleaned_data[f])}
                for f in form.changed_data
            }
            form.save()
            _log(request, "change", "auth.user", target_user.pk, str(target_user),
                 {"name": "edit_user", "changes": changed})
            messages.success(request, "User updated.")
            return redirect("ops_user_detail", user_id=target_user.pk)
    else:
        form = OpsUserEditForm(instance=target_user)

    membership = getattr(target_user, "school_membership", None)
    recent_audit = AdminAuditLog.objects.filter(
        model_label="auth.user", object_id=str(target_user.pk)
    ).order_by("-created_at")[:20]

    return render(request, "ops/user_detail.html", {
        "active_nav": "users",
        "target_user": target_user,
        "form": form,
        "membership": membership,
        "recent_audit": recent_audit,
    })


@ops_required
def ops_user_create_view(request):
    from core.forms_ops import OpsUserCreateForm

    if request.method == "POST":
        form = OpsUserCreateForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data["password"])
            user.save()
            _log(request, "add", "auth.user", user.pk, str(user),
                 {"name": "create_user", "email": user.email})

            school = form.cleaned_data.get("school")
            if school:
                if hasattr(user, "school_membership"):
                    messages.warning(request, f"User already has a school assignment — skipped.")
                else:
                    SchoolAdminMembership.objects.create(user=user, school=school)
                    _log(request, "add", "core.schooladminmembership", user.pk,
                         f"{user.email} → {school.slug}",
                         {"name": "add_member", "email": user.email, "school": school.slug})

            messages.success(request, f"User '{user.email or user.username}' created.")
            return redirect("ops_user_detail", user_id=user.pk)
    else:
        form = OpsUserCreateForm()

    return render(request, "ops/user_form.html", {
        "active_nav": "users",
        "form": form,
        "form_title": "Create User",
        "submit_label": "Create",
    })


@ops_required
@require_POST
def ops_user_deactivate_view(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    if target_user.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("ops_user_detail", user_id=user_id)
    new_state = not target_user.is_active
    target_user.is_active = new_state
    target_user.save(update_fields=["is_active"])
    action_label = "activated" if new_state else "deactivated"
    _log(request, "change", "auth.user", target_user.pk, str(target_user),
         {"name": f"user_{action_label}", "email": target_user.email})
    messages.success(request, f"User {action_label}.")
    return redirect("ops_user_detail", user_id=user_id)


@ops_required
@require_POST
def ops_user_reset_password_view(request, user_id):
    target_user = get_object_or_404(User, pk=user_id)
    new_password = request.POST.get("new_password", "").strip()
    confirm = request.POST.get("confirm_password", "").strip()

    if not new_password:
        messages.error(request, "Password cannot be blank.")
        return redirect("ops_user_detail", user_id=user_id)
    if len(new_password) < 8:
        messages.error(request, "Password must be at least 8 characters.")
        return redirect("ops_user_detail", user_id=user_id)
    if new_password != confirm:
        messages.error(request, "Passwords do not match.")
        return redirect("ops_user_detail", user_id=user_id)

    target_user.set_password(new_password)
    target_user.save(update_fields=["password"])
    _log(request, "change", "auth.user", target_user.pk, str(target_user),
         {"name": "reset_password", "email": target_user.email})
    messages.success(request, f"Password reset for {target_user.email or target_user.username}.")
    return redirect("ops_user_detail", user_id=user_id)


# ── Cross-school submissions ──────────────────────────────────────────────────

_OPS_PAGE_SIZE = 50


@ops_required
def ops_submissions_view(request):
    from core.views import STATUS_ENROLLED, STATUS_DECLINED

    qs = Submission.objects.select_related("school").order_by("-created_at")

    school_filter = request.GET.get("school", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    form_key_filter = request.GET.get("form_key", "").strip()

    if school_filter:
        qs = qs.filter(school__slug=school_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_from:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass
    if form_key_filter:
        qs = qs.filter(form_key=form_key_filter)

    total_count = qs.count()
    paginator = Paginator(qs, _OPS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    all_schools = School.objects.order_by("display_name", "slug").values("slug", "display_name")
    status_choices = (
        Submission.objects.values_list("status", flat=True)
        .distinct().order_by("status")
    )
    form_key_choices = (
        Submission.objects.values_list("form_key", flat=True)
        .distinct().order_by("form_key")
    )

    return render(request, "ops/submissions.html", {
        "active_nav": "submissions",
        "page_obj": page_obj,
        "total_count": total_count,
        "all_schools": all_schools,
        "status_choices": status_choices,
        "form_key_choices": form_key_choices,
        "school_filter": school_filter,
        "status_filter": status_filter,
        "date_from": date_from,
        "date_to": date_to,
        "form_key_filter": form_key_filter,
    })


# ── Cross-school leads ────────────────────────────────────────────────────────

@ops_required
def ops_leads_view(request):
    from core.models import LEAD_STATUS_CHOICES, LEAD_SOURCE_CHOICES

    qs = Lead.objects.select_related("school").order_by("-created_at")

    school_filter = request.GET.get("school", "").strip()
    status_filter = request.GET.get("status", "").strip()
    source_filter = request.GET.get("source", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    search_q = request.GET.get("q", "").strip()

    if school_filter:
        qs = qs.filter(school__slug=school_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if source_filter:
        qs = qs.filter(source=source_filter)
    if date_from:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass
    if search_q:
        qs = qs.filter(
            Q(name__icontains=search_q) | Q(email__icontains=search_q)
        )

    total_count = qs.count()
    paginator = Paginator(qs, _OPS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    all_schools = School.objects.order_by("display_name", "slug").values("slug", "display_name")

    return render(request, "ops/leads.html", {
        "active_nav": "leads",
        "page_obj": page_obj,
        "total_count": total_count,
        "all_schools": all_schools,
        "lead_status_choices": LEAD_STATUS_CHOICES,
        "lead_source_choices": LEAD_SOURCE_CHOICES,
        "school_filter": school_filter,
        "status_filter": status_filter,
        "source_filter": source_filter,
        "date_from": date_from,
        "date_to": date_to,
        "search_q": search_q,
    })


# ── Cross-school reports ──────────────────────────────────────────────────────

@ops_required
def ops_reports_view(request):
    from core.views import STATUS_ENROLLED

    school_rows = list(
        School.objects.annotate(
            sub_count=Count("submissions", distinct=True),
            lead_count=Count("leads", distinct=True),
            converted_lead_count=Count(
                "leads",
                filter=Q(leads__converted_submission__isnull=False),
                distinct=True,
            ),
            enrolled_count=Count(
                "submissions",
                filter=Q(submissions__status=STATUS_ENROLLED),
                distinct=True,
            ),
        ).order_by("-sub_count", "display_name")
    )

    for row in school_rows:
        # Lead→App: of the leads captured, what % converted to an application?
        row.lead_to_sub_rate = (
            round(row.converted_lead_count / row.lead_count * 100)
            if row.lead_count else None
        )
        row.sub_to_enrolled_rate = (
            round(row.enrolled_count / row.sub_count * 100) if row.sub_count else None
        )

    totals = {
        "schools": len(school_rows),
        "leads": sum(r.lead_count for r in school_rows),
        "converted_leads": sum(r.converted_lead_count for r in school_rows),
        "submissions": sum(r.sub_count for r in school_rows),
        "enrolled": sum(r.enrolled_count for r in school_rows),
    }
    totals["lead_to_sub_rate"] = (
        round(totals["converted_leads"] / totals["leads"] * 100)
        if totals["leads"] else None
    )
    totals["sub_to_enrolled_rate"] = (
        round(totals["enrolled"] / totals["submissions"] * 100)
        if totals["submissions"] else None
    )

    return render(request, "ops/reports.html", {
        "active_nav": "reports",
        "school_rows": school_rows,
        "totals": totals,
    })


# ── Demo access tokens ─────────────────────────────────────────────────────────

@ops_required
@require_POST
def ops_demo_token_generate_view(request, slug):
    school = get_object_or_404(School, slug=slug)
    expires_at = timezone.now() + timezone.timedelta(days=14)
    token = DemoAccessToken.objects.create(
        school=school,
        expires_at=expires_at,
        created_by=request.user,
    )
    _log(request, "action", "core.demoaccesstoken", token.pk, str(token),
         {"name": "generate_demo_token", "school": slug})
    messages.success(request, f"Demo link generated — expires {expires_at.strftime('%b %d, %Y')}.")
    return redirect("ops_school_detail", slug=slug)


# ── Onboarding: Convert Demo to Customer ─────────────────────────────────────

@ops_required
def ops_school_convert_view(request, slug):
    from core.services.feature_flags import ALL_PLANS, PLAN_CHOICES
    from core.services.onboarding import convert_demo_to_customer
    from core.services.url_builder import app_reverse

    school = get_object_or_404(School, slug=slug)
    sub_count = Submission.objects.filter(school=school).count()
    lead_count = Lead.objects.filter(school=school).count()

    ctx = {
        "active_nav": "schools",
        "school": school,
        "sub_count": sub_count,
        "lead_count": lead_count,
        "plan_choices": [(p, label) for p, label in PLAN_CHOICES if p != "trial" or True],
        "errors": [],
        "post": {},
        "conversion_result": None,
    }

    if request.method != "POST":
        return render(request, "ops/school_convert.html", ctx)

    admin_email = request.POST.get("admin_email", "").strip()
    admin_first_name = request.POST.get("admin_first_name", "").strip()
    admin_last_name = request.POST.get("admin_last_name", "").strip()
    plan = request.POST.get("plan", "trial").strip()
    trial_days_raw = request.POST.get("trial_days", "30").strip()
    delete_submissions = request.POST.get("delete_submissions") == "1"
    delete_leads = request.POST.get("delete_leads") == "1"

    errors = []
    if not admin_email or "@" not in admin_email:
        errors.append("Valid admin email is required.")
    if plan not in ALL_PLANS:
        errors.append(f"Invalid plan '{plan}'.")
    trial_days = None
    if plan == "trial":
        try:
            trial_days = int(trial_days_raw)
            if not (1 <= trial_days <= 365):
                raise ValueError
        except (ValueError, TypeError):
            errors.append("Trial days must be a number between 1 and 365.")

    if errors:
        ctx.update({"errors": errors, "post": request.POST})
        return render(request, "ops/school_convert.html", ctx)

    result = convert_demo_to_customer(
        school=school,
        plan=plan,
        trial_days=trial_days,
        admin_email=admin_email,
        admin_first_name=admin_first_name,
        admin_last_name=admin_last_name,
        delete_submissions=delete_submissions,
        delete_leads=delete_leads,
        actor=request.user,
    )

    magic_link = app_reverse("demo_access", kwargs={"token": result["magic_token"].token})
    enrollment_url = app_reverse("apply", kwargs={"school_slug": school.slug})
    iframe_snippet = (
        f'<iframe src="{enrollment_url}" width="100%" height="800" '
        f'frameborder="0" style="border:none;"></iframe>'
    )

    ctx.update({
        "conversion_result": result,
        "magic_link": magic_link,
        "enrollment_url": enrollment_url,
        "iframe_snippet": iframe_snippet,
        "sub_count": Submission.objects.filter(school=school).count(),
        "lead_count": Lead.objects.filter(school=school).count(),
    })
    return render(request, "ops/school_convert.html", ctx)


# ── Onboarding: Checklist Toggle ──────────────────────────────────────────────

@ops_required
@require_POST
def ops_checklist_toggle_view(request, slug, item):
    school = get_object_or_404(School, slug=slug)
    if item not in OnboardingChecklistItem.ITEM_LABELS:
        messages.error(request, f"Unknown checklist item '{item}'.")
        return redirect("ops_school_detail", slug=slug)

    from core.services.onboarding import mark_checklist_item, unmark_checklist_item
    obj, _ = OnboardingChecklistItem.objects.get_or_create(school=school, item=item)
    if obj.completed_at:
        unmark_checklist_item(school, item, request.user)
    else:
        mark_checklist_item(school, item, request.user)

    return redirect("ops_school_detail", slug=slug)


# ── Onboarding: Welcome Email ─────────────────────────────────────────────────

@ops_required
@require_POST
def ops_school_welcome_email_view(request, slug):
    school = get_object_or_404(School, slug=slug)
    from core.services.onboarding import send_welcome_email
    ok = send_welcome_email(school, request.user)
    if ok:
        messages.success(request, "Welcome email sent.")
    else:
        messages.error(request, "Failed to send welcome email. Check that an admin with an email address exists and that email is configured.")
    return redirect("ops_school_detail", slug=slug)


@ops_required
@require_POST
def ops_demo_token_extend_view(request, slug):
    school = get_object_or_404(School, slug=slug)
    token = DemoAccessToken.objects.filter(school=school).order_by("-created_at").first()
    if not token:
        messages.error(request, "No demo token exists for this school. Generate one first.")
        return redirect("ops_school_detail", slug=slug)
    base = max(token.expires_at, timezone.now())
    token.expires_at = base + timezone.timedelta(days=14)
    token.save(update_fields=["expires_at"])
    _log(request, "action", "core.demoaccesstoken", token.pk, str(token),
         {"name": "extend_demo_token", "school": slug,
          "new_expires_at": token.expires_at.isoformat()})
    messages.success(request, f"Demo link extended to {token.expires_at.strftime('%b %d, %Y')}.")
    return redirect("ops_school_detail", slug=slug)


# ── Audit log ─────────────────────────────────────────────────────────────────

@ops_required
def ops_audit_log_view(request):
    qs = AdminAuditLog.objects.select_related("actor").order_by("-created_at")

    actor_filter = request.GET.get("actor", "").strip()
    action_filter = request.GET.get("action", "").strip()
    model_filter = request.GET.get("model", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    search_q = request.GET.get("q", "").strip()

    if actor_filter:
        qs = qs.filter(actor__username__icontains=actor_filter)
    if action_filter:
        qs = qs.filter(action=action_filter)
    if model_filter:
        qs = qs.filter(model_label__icontains=model_filter)
    if date_from:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import date
            qs = qs.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            pass
    if search_q:
        qs = qs.filter(
            Q(object_repr__icontains=search_q)
            | Q(actor__username__icontains=search_q)
            | Q(extra__icontains=search_q)
            | Q(path__icontains=search_q)
        )

    total_count = qs.count()
    paginator = Paginator(qs, _OPS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))

    actor_choices = (
        AdminAuditLog.objects
        .exclude(actor__isnull=True)
        .values_list("actor__username", flat=True)
        .distinct()
        .order_by("actor__username")
    )

    return render(request, "ops/audit_log.html", {
        "active_nav": "audit",
        "page_obj": page_obj,
        "total_count": total_count,
        "action_choices": AdminAuditLog.ACTION_CHOICES,
        "actor_choices": actor_choices,
        "actor_filter": actor_filter,
        "action_filter": action_filter,
        "model_filter": model_filter,
        "date_from": date_from,
        "date_to": date_to,
        "search_q": search_q,
    })
