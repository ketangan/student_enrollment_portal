"""
Ops portal views — /ops/ prefix, superuser-only.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import AdminAuditLog, School, SchoolAdminMembership


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

    return render(request, "ops/dashboard.html", {
        "active_nav": "dashboard",
        "total_schools": total_schools,
        "active_schools": active_schools,
        "inactive_schools": inactive_schools,
        "expiring_soon": expiring_soon,
        "expired_trials": expired,
        "total_users": total_users,
        "recent_schools": recent_schools,
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

    return render(request, "ops/school_detail.html", {
        "active_nav": "schools",
        "school": school,
        "form": form,
        "members": members,
        "recent_audit": recent_audit,
        "submission_count": submission_count,
        "lead_count": lead_count,
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
            messages.success(request, f"User '{user.email}' created.")
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
