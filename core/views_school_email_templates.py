"""School admin views for managing email templates."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.http import require_http_methods

from core.admin.audit import log_admin_audit
from core.models import SchoolEmailTemplate
from core.views_school_common import _get_accessible_school_for_admin, _school_admin_base_context

_SETTINGS_URL = lambda slug: reverse("school_settings", kwargs={"school_slug": slug})

_MAX_SUBJECT = 255
_MAX_NAME    = 120

_TOKENS = [
    ("full_name",   "Student full name"),
    ("first_name",  "Student first name"),
    ("email",       "Applicant email"),
    ("program",     "Program / class"),
    ("status",      "Application status"),
    ("school_name", "School name"),
]


def _validate_template(name, subject, body):
    errors = {}
    if not name:
        errors["name"] = "Name is required."
    elif len(name) > _MAX_NAME:
        errors["name"] = f"Name must be {_MAX_NAME} characters or fewer."
    if not subject:
        errors["subject"] = "Subject is required."
    elif len(subject) > _MAX_SUBJECT:
        errors["subject"] = f"Subject must be {_MAX_SUBJECT} characters or fewer."
    if not strip_tags(body).strip():
        errors["body"] = "Body cannot be empty."
    return errors


@login_required
@require_http_methods(["GET", "POST"])
def school_email_template_create_view(request, school_slug: str):
    school = _get_accessible_school_for_admin(request, school_slug)
    back_url = _SETTINGS_URL(school_slug)

    errors = {}
    values = {"name": "", "subject": "", "body": ""}

    if request.method == "POST":
        name    = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body    = request.POST.get("body", "").strip()
        values  = {"name": name, "subject": subject, "body": body}
        errors  = _validate_template(name, subject, body)

        if not errors:
            tmpl = SchoolEmailTemplate.objects.create(
                school=school, name=name, subject=subject, body=body
            )
            log_admin_audit(
                request=request, action="add", obj=tmpl, changes={},
                extra={"name": "email_template_created", "template_name": name},
            )
            messages.success(request, f'Template "{name}" saved.')
            return redirect(back_url)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": "Add Email Template",
        "form_action": request.path,
        "back_url": back_url,
        "errors": errors,
        "values": values,
        "is_edit": False,
        "tokens": _TOKENS,
    })
    return render(request, "school_admin/email_template_form.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_email_template_edit_view(request, school_slug: str, template_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    tmpl   = get_object_or_404(SchoolEmailTemplate, id=template_id, school=school)
    back_url = _SETTINGS_URL(school_slug)

    errors = {}
    values = {"name": tmpl.name, "subject": tmpl.subject, "body": tmpl.body}

    if request.method == "POST":
        name    = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body    = request.POST.get("body", "").strip()
        values  = {"name": name, "subject": subject, "body": body}
        errors  = _validate_template(name, subject, body)

        if not errors:
            changed = {}
            if tmpl.name != name:       changed["name"]    = {"old": tmpl.name,    "new": name}
            if tmpl.subject != subject: changed["subject"] = {"old": tmpl.subject, "new": subject}
            if tmpl.body != body:       changed["body"]    = "updated"

            tmpl.name    = name
            tmpl.subject = subject
            tmpl.body    = body
            tmpl.save()

            log_admin_audit(
                request=request, action="change", obj=tmpl, changes=changed,
                extra={"name": "email_template_updated", "template_name": name},
            )
            messages.success(request, f'Template "{name}" updated.')
            return redirect(back_url)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": f"Edit: {tmpl.name}",
        "form_action": request.path,
        "back_url": back_url,
        "errors": errors,
        "values": values,
        "is_edit": True,
        "template": tmpl,
        "tokens": _TOKENS,
        "delete_url": reverse("school_email_template_delete", kwargs={"school_slug": school_slug, "template_id": tmpl.pk}),
    })
    return render(request, "school_admin/email_template_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_email_template_delete_view(request, school_slug: str, template_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    tmpl   = get_object_or_404(SchoolEmailTemplate, id=template_id, school=school)
    name   = tmpl.name
    log_admin_audit(
        request=request, action="delete", obj=tmpl, changes={},
        extra={"name": "email_template_deleted", "template_name": name},
    )
    tmpl.delete()
    messages.success(request, f'Template "{name}" deleted.')
    return redirect(_SETTINGS_URL(school_slug))
