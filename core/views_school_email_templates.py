"""School admin views for managing email templates."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.http import require_http_methods

import re as _re

from core.admin.audit import log_admin_audit
from core.models import SchoolCustomToken, SchoolEmailTemplate
from core.services.school_permissions import require_school_role
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
    require_school_role(request, school, "editor")
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
        "custom_tokens": list(SchoolCustomToken.objects.filter(school=school)),
    })
    return render(request, "school_admin/email_template_form.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_email_template_edit_view(request, school_slug: str, template_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
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
        "custom_tokens": list(SchoolCustomToken.objects.filter(school=school)),
        "delete_url": reverse("school_email_template_delete", kwargs={"school_slug": school_slug, "template_id": tmpl.pk}),
    })
    return render(request, "school_admin/email_template_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_email_template_delete_view(request, school_slug: str, template_id: int):
    """Deactivate (soft-delete) a template so it no longer appears in compose dropdowns."""
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
    tmpl   = get_object_or_404(SchoolEmailTemplate, id=template_id, school=school)
    name   = tmpl.name
    tmpl.is_active = False
    tmpl.save(update_fields=["is_active"])
    log_admin_audit(
        request=request, action="action", obj=tmpl, changes={},
        extra={"name": "email_template_deactivated", "template_name": name},
    )
    messages.success(request, f'Template "{name}" deactivated.')
    return redirect(_SETTINGS_URL(school_slug))


@login_required
@require_http_methods(["POST"])
def school_email_template_reactivate_view(request, school_slug: str, template_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
    tmpl   = get_object_or_404(SchoolEmailTemplate, id=template_id, school=school)
    name   = tmpl.name
    tmpl.is_active = True
    tmpl.save(update_fields=["is_active"])
    log_admin_audit(
        request=request, action="action", obj=tmpl, changes={},
        extra={"name": "email_template_reactivated", "template_name": name},
    )
    messages.success(request, f'Template "{name}" reactivated.')
    return redirect(_SETTINGS_URL(school_slug))


_TOKEN_KEY_RE = _re.compile(r'^[a-z][a-z0-9_]*$')


def _token_redirect(request, school_slug: str):
    """Redirect to `next` POST param if safe, else fall back to settings."""
    next_url = request.POST.get("next", "").strip()
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(_SETTINGS_URL(school_slug))


def _templates_using_token(school, key: str) -> list:
    """Return all templates (active or inactive) whose body or subject contain {{key}}."""
    pattern = _re.compile(r'\{\{' + _re.escape(key) + r'\}\}')
    return [
        t for t in SchoolEmailTemplate.objects.filter(school=school)
        if pattern.search(t.body) or pattern.search(t.subject)
    ]


@login_required
@require_http_methods(["POST"])
def school_custom_token_create_view(request, school_slug: str):
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
    key    = request.POST.get("key", "").strip().lower()
    label  = request.POST.get("label", "").strip()

    if not key or not _TOKEN_KEY_RE.match(key):
        messages.error(request, "Token key must start with a letter and contain only lowercase letters, numbers, and underscores.")
        return _token_redirect(request, school_slug)
    if not label:
        messages.error(request, "Token label is required.")
        return _token_redirect(request, school_slug)
    if len(key) > 50:
        messages.error(request, "Token key must be 50 characters or fewer.")
        return _token_redirect(request, school_slug)

    _, created = SchoolCustomToken.objects.get_or_create(
        school=school, key=key, defaults={"label": label}
    )
    if created:
        log_admin_audit(
            request=request, action="add", obj=school, changes={},
            extra={"name": "custom_token_created", "key": key, "label": label},
        )
        messages.success(request, f'Token "{key}" added.')
    else:
        messages.warning(request, f'Token "{key}" already exists.')
    return _token_redirect(request, school_slug)


@login_required
@require_http_methods(["POST"])
def school_custom_token_delete_view(request, school_slug: str, token_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
    token  = get_object_or_404(SchoolCustomToken, id=token_id, school=school)
    key    = token.key
    next_url = request.POST.get("next", "").strip()

    affected = _templates_using_token(school, key)
    confirmed = request.POST.get("confirm") == "1"

    if affected and not confirmed:
        # Show confirmation page listing which templates will be orphaned
        ctx = _school_admin_base_context(request, school, "settings")
        ctx.update({
            "token": token,
            "affected_templates": affected,
            "delete_url": request.path,
            "next_url": next_url,
            "cancel_url": next_url or _SETTINGS_URL(school_slug),
        })
        return render(request, "school_admin/email_template_token_confirm_delete.html", ctx)

    affected_names = [t.name for t in affected]
    log_admin_audit(
        request=request, action="delete", obj=school, changes={},
        extra={
            "name": "custom_token_deleted",
            "key": key,
            "label": token.label,
            "affected_templates": affected_names,
            "deleted_despite_usage": bool(affected_names),
        },
    )
    token.delete()

    if affected_names:
        messages.warning(
            request,
            f'Token "{key}" deleted. '
            f'{len(affected_names)} template(s) still reference it and will show the placeholder as-is: '
            f'{", ".join(affected_names)}.',
        )
    else:
        messages.success(request, f'Token "{key}" removed.')
    return _token_redirect(request, school_slug)
