# core/services/notifications.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.http import HttpRequest
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape
from django.urls import reverse

logger = logging.getLogger(__name__)


def get_school_email_connection(school=None):
    """Return an email connection for the given school.

    If the school has SMTP configured (smtp_host set), opens a direct SMTP
    connection using the school's own credentials so emails arrive from their
    mail server.  Falls back to the global Resend backend otherwise.
    """
    timeout = getattr(settings, "EMAIL_TIMEOUT", 10)
    if school and getattr(school, "smtp_host", ""):
        from django.core.mail.backends.smtp import EmailBackend
        return EmailBackend(
            host=school.smtp_host,
            port=school.smtp_port or 587,
            username=school.smtp_username or "",
            password=school.smtp_password or "",
            use_tls=school.smtp_use_tls,
            timeout=timeout,
            fail_silently=False,
        )
    return get_connection(timeout=timeout)


# ----------------------------
# Helpers
# ----------------------------

def _split_emails(raw: str | None) -> List[str]:
    if not raw:
        return []
    # comma-separated list
    parts = [p.strip() for p in raw.split(",")]
    # keep only non-empty items
    return [p for p in parts if p]


def _get_nested(d: dict, path: List[str], default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _render_template(s: str, context: Dict[str, Any]) -> str:
    """
    Minimal safe template: replaces {{key}} occurrences.
    (No conditionals; MVP-only, intentionally simple.)
    """
    out = s or ""
    for k, v in (context or {}).items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _format_submission_lines(submission_data: Dict[str, Any]) -> str:
    """
    Simple plaintext body: key: value lines.
    Keeps this MVP-friendly; later you can render sections/labels.
    """
    if not isinstance(submission_data, dict):
        return ""
    lines = []
    for k in sorted(submission_data.keys()):
        v = submission_data.get(k)
        if isinstance(v, list):
            v = ", ".join([str(x) for x in v])
        lines.append(f"{k}: {v}")
    return "\n".join(lines)

def _admin_url_for_submission(
    *, request: Optional[HttpRequest], submission_id: int | str
) -> str:
    from core.services.url_builder import app_url
    path = reverse("admin:core_submission_change", args=[submission_id])
    return app_url(path)


def _build_submission_email_subject(*, student_name: str, program: str) -> str:
    return f"New submission: {student_name}" + (f" ({program})" if program else "")


def _build_submission_email_bodies(
    *,
    submission_public_id: str,
    student_name: str,
    program: str,
    admin_url: str,
) -> Tuple[str, str]:
    # Plain text (fallback)
    text_body = "\n".join([
        "New submission received",
        f"Application ID: {submission_public_id}",
        f"Student: {student_name}",
        f"Program: {program}" if program else "",
        "",
        f"View in admin: {admin_url}",
        "",
        "— student_enrollment_portal",
    ])

    # HTML (nice link)
    html_body = f"""
    <p><strong>New submission received</strong></p>

    <p>
            <strong>Application ID:</strong> {escape(submission_public_id)}<br/>
      <strong>Student:</strong> {escape(student_name)}<br/>
      {f"<strong>Program:</strong> {escape(program)}<br/>" if program else ""}
    </p>

    <p>
      <a href="{admin_url}"
         target="_blank"
         style="
           display:inline-block;
           padding:10px 14px;
           background:#2563eb;
           color:#ffffff;
           text-decoration:none;
           border-radius:6px;
           font-weight:600;
         ">
        View submission in admin
      </a>
    </p>

    <hr/>
    <p style="color:#666;font-size:12px;">
      student_enrollment_portal
    </p>
    """
    return text_body, html_body

def _pick_program_label(submission_data: dict) -> str:
    # For now: keep it simple and stable
    val = (submission_data or {}).get("program_interest") or ""
    return str(val).strip()


def _collect_email_field_keys(config_raw: dict) -> List[str]:
    """
    Scans all form sections (single-form and multi-form) for fields of type=email.
    Returns an ordered list of keys: required fields first, then optional.
    """
    raw = config_raw or {}
    all_sections: List[dict] = []

    # Single-form
    single_form = raw.get("form") or {}
    if isinstance(single_form, dict):
        all_sections.extend(single_form.get("sections") or [])

    # Multi-form
    forms_dict = raw.get("forms") or {}
    if isinstance(forms_dict, dict):
        for form_data in forms_dict.values():
            if isinstance(form_data, dict):
                form_inner = form_data.get("form") or {}
                if isinstance(form_inner, dict):
                    all_sections.extend(form_inner.get("sections") or [])

    required_keys: List[str] = []
    optional_keys: List[str] = []

    for section in all_sections:
        for field in (section.get("fields") or []):
            if (field.get("type") or "").strip().lower() == "email":
                key = field.get("key")
                if key:
                    if field.get("required"):
                        required_keys.append(key)
                    else:
                        optional_keys.append(key)

    return required_keys + optional_keys


def _find_email_field_key(config_raw: dict) -> Optional[str]:
    """
    Returns the highest-priority email field key from YAML (required > optional),
    or None if no type=email field is declared.
    Used when building a submission that must be keyed to match the YAML's email field.
    """
    keys = _collect_email_field_keys(config_raw)
    return keys[0] if keys else None


def _find_applicant_email(submission_data: dict, config_raw: dict) -> Optional[str]:
    """
    Scans all form sections (single-form and multi-form) for fields of type=email.
    Prefers required fields over optional. Returns the first non-empty value found
    in submission_data, or None if no email field/value exists.
    """
    data = submission_data or {}
    for key in _collect_email_field_keys(config_raw):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return None


def _build_confirmation_email_bodies(
    *,
    school_name: str,
    student_name: str,
    submission_public_id: str,
    response_time: str,
    custom_message: str,
    scheduling_url: str = "",
    scheduling_label: str = "Book a time",
    status_url: str = "",
) -> Tuple[str, str]:
    """Builds plain text and HTML bodies for the applicant confirmation email."""
    default_message = (
        f"Thanks for applying to {school_name}. "
        "We've received your application and will be in touch soon."
    )
    body_message = custom_message.strip() if custom_message.strip() else default_message

    # Plain text
    lines = [
        f"Hi {student_name}," if student_name else "Hi,",
        "",
        body_message,
        "",
        f"Application ID: {submission_public_id}",
    ]
    if response_time:
        lines.append(f"Expected response time: {response_time}")
    if status_url:
        lines += ["", f"Track your application status: {status_url}"]
    if scheduling_url:
        lines += ["", f"{scheduling_label}: {scheduling_url}"]
    lines += ["", f"— {school_name}"]
    text_body = "\n".join(lines)

    # HTML
    greeting = f"Hi {escape(student_name)}," if student_name else "Hi,"
    response_line = (
        f"<p><strong>Expected response time:</strong> {escape(response_time)}</p>"
        if response_time
        else ""
    )
    status_line = (
        f'<p><a href="{escape(status_url)}" style="color:#2563eb;font-weight:600;">'
        f"Track your application status →</a></p>"
        if status_url
        else ""
    )
    scheduling_line = (
        f'<p><a href="{escape(scheduling_url)}" style="color:#2563eb;font-weight:600;">'
        f"{escape(scheduling_label)}</a></p>"
        if scheduling_url
        else ""
    )
    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;width:100%;margin:0 auto;">
      <p>{greeting}</p>
      <p>{escape(body_message)}</p>
      <p>
        <strong>Application ID:</strong> {escape(submission_public_id)}
      </p>
      {response_line}
      {status_line}
      {scheduling_line}
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;"/>
      <p style="color:#666;font-size:12px;">{escape(school_name)}</p>
    </div>
    """
    return text_body, html_body


# ----------------------------
# Public API
# ----------------------------

@dataclass(frozen=True)
class ApplicantConfirmationConfig:
    from_email: str
    subject: str
    message: str  # empty string = use default message


def get_applicant_confirmation_config(config_raw: Dict[str, Any]) -> Optional["ApplicantConfirmationConfig"]:
    """
    Parses config_raw["success"]["notifications"]["applicant_confirmation"].
    Returns None if block is missing, enabled is falsy, or from_email cannot be resolved.
    """
    if not isinstance(config_raw, dict):
        return None

    block = _get_nested(config_raw, ["success", "notifications", "applicant_confirmation"], default=None)
    if not isinstance(block, dict):
        return None

    if not block.get("enabled"):
        return None

    from_email = (block.get("from_email") or "").strip() or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if not from_email:
        return None

    subject = (block.get("subject") or "").strip()
    message = (block.get("message") or "").strip()

    return ApplicantConfirmationConfig(
        from_email=from_email,
        subject=subject,
        message=message,
    )


def send_applicant_confirmation_email(
    *,
    config_raw: Dict[str, Any],
    school_name: str,
    submission_public_id: str,
    student_name: str,
    submission_data: Dict[str, Any],
    status_url: str = "",
    school=None,
) -> bool:
    """
    Sends a confirmation email to the applicant after successful submission.
    Returns True if sent, False if skipped or failed.
    """
    cfg = get_applicant_confirmation_config(config_raw)
    if not cfg:
        return False

    applicant_email = _find_applicant_email(submission_data, config_raw)
    if not applicant_email:
        logger.warning(
            "applicant_confirmation enabled but no email field found in submission data"
        )
        return False

    response_time = str(
        _get_nested(config_raw, ["success", "response_time"], default="") or ""
    ).strip()

    scheduling_cfg = (config_raw.get("scheduling") or {}) if isinstance(config_raw, dict) else {}
    scheduling_url = (scheduling_cfg.get("url") or "").strip()
    scheduling_label = (scheduling_cfg.get("label") or "").strip() or "Book a time"

    template_context = {
        "student_name": student_name,
        "school_name": school_name,
        "application_id": submission_public_id,
        "response_time": response_time,
    }

    default_subject = f"We received your application to {school_name}"
    raw_subject = cfg.subject or default_subject
    subject = _render_template(raw_subject, template_context)

    custom_message = _render_template(cfg.message, template_context)
    text_body, html_body = _build_confirmation_email_bodies(
        school_name=school_name,
        student_name=student_name,
        submission_public_id=submission_public_id,
        response_time=response_time,
        custom_message=custom_message,
        scheduling_url=scheduling_url,
        scheduling_label=scheduling_label,
        status_url=status_url,
    )

    try:
        conn = get_school_email_connection(school)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=cfg.from_email,
            to=[applicant_email],
            connection=conn,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True

    except Exception:
        logger.exception("Failed to send applicant confirmation email")
        return False


@dataclass(frozen=True)
class SubmissionEmailConfig:
    to: List[str]
    cc: List[str]
    bcc: List[str]
    from_email: str
    subject: str


def get_submission_email_config(config_raw: Dict[str, Any]) -> Optional[SubmissionEmailConfig]:
    """
    Expects YAML under:
      success:
        notifications:
          submission_email:
            to: "a@x.com, b@y.com"
            cc: ""
            bcc: ""
            from_email: "verified@sender.com"
            subject: "New submission: {{student_name}}"
    """
    if not isinstance(config_raw, dict):
        return None

    block = _get_nested(config_raw, ["success", "notifications", "submission_email"], default=None)
    if not isinstance(block, dict):
        return None

    to_list = _split_emails(block.get("to"))
    cc_list = _split_emails(block.get("cc"))
    bcc_list = _split_emails(block.get("bcc"))

    from_email = (block.get("from_email") or "").strip() or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    subject = (block.get("subject") or "New submission").strip()

    if not to_list:
        # no recipients => treat as disabled
        return None

    return SubmissionEmailConfig(
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        from_email=from_email,
        subject=subject,
    )


def send_resume_link_email(*, draft, school) -> bool:
    """
    Email the applicant their magic resume link.
    Returns True if sent, False if skipped (no email) or failed.
    Uses DEFAULT_FROM_EMAIL — same Resend config used across the app.
    """
    if not draft.email:
        return False

    from core.services.url_builder import app_reverse
    resume_url = app_reverse("apply_resume", args=[school.slug, draft.token])
    school_name = school.display_name or school.slug
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if not from_email:
        logger.warning("send_resume_link_email: DEFAULT_FROM_EMAIL not configured; skipping")
        return False

    subject = f"Continue your application to {school_name}"
    body = (
        f"Hi,\n\n"
        f"You saved your application to {school_name}. "
        f"Click the link below to continue where you left off:\n\n"
        f"{resume_url}\n\n"
        f"This link expires in 7 days.\n\n"
        f"If you did not request this email, you can ignore it.\n\n"
        f"— {school_name}"
    )
    try:
        conn = get_school_email_connection(school)
        msg = EmailMessage(subject, body, from_email, [draft.email], connection=conn)
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send resume link email to %s", draft.email)
        return False


def send_status_link_email(*, to_email: str, status_url: str, school_name: str, school=None) -> bool:
    """
    Email the parent a link to their application's family status page.
    Returns True if sent, False if skipped or failed.
    """
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if not from_email:
        logger.warning("send_status_link_email: DEFAULT_FROM_EMAIL not configured; skipping")
        return False

    subject = f"Your application status — {school_name}"
    body = (
        f"Hi,\n\n"
        f"You can check the status of your application to {school_name} at the link below:\n\n"
        f"{status_url}\n\n"
        f"— {school_name}"
    )
    try:
        conn = get_school_email_connection(school)
        msg = EmailMessage(subject, body, from_email, [to_email], connection=conn)
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send status link email to %s", to_email)
        return False


def send_submission_notification_email(
    *,
    request: Optional[HttpRequest],
    config_raw: Dict[str, Any],
    school_name: str,
    submission_id: int | str,
    submission_public_id: str,
    student_name: str,
    submission_data: Dict[str, Any],
    school=None,
) -> bool:
    """
    Sends email notification on new submission.
    Returns True if email sent, False if skipped or failed.
    """
    cfg = get_submission_email_config(config_raw)
    if not cfg:
        return False

    program = _pick_program_label(submission_data)
    admin_url = _admin_url_for_submission(request=request, submission_id=submission_id)
    raw_subject = cfg.subject or _build_submission_email_subject(student_name=student_name, program=program)
    subject = _render_template(raw_subject, {"student_name": student_name, "program": program})
    text_body, html_body = _build_submission_email_bodies(
        submission_public_id=submission_public_id,
        student_name=student_name,
        program=program,
        admin_url=admin_url,
    )

    try:
        conn = get_school_email_connection(school)

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=cfg.from_email,
            to=cfg.to,
            cc=cfg.cc,
            bcc=cfg.bcc,
            connection=conn,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True

    except Exception:
        logger.exception("Failed to send submission notification email")
        return False



def _resolve_from_email(config_raw: dict) -> str:
    """
    Returns the school-configured from_email (from YAML applicant_confirmation block)
    or falls back to settings.DEFAULT_FROM_EMAIL.
    """
    candidate = _get_nested(
        config_raw,
        ["success", "notifications", "applicant_confirmation", "from_email"],
        default="",
    )
    if candidate and isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return getattr(settings, "DEFAULT_FROM_EMAIL", "")



def send_admin_message(
    *,
    to_email: str,
    subject: str,
    message: str,
    school_name: str,
    from_email: str | None = None,
    is_html: bool = False,
    school=None,
) -> bool:
    """
    Sends a one-off admin-composed message to a family member.
    When is_html=True the message is treated as trusted HTML (from the template
    editor) \u2014 it is embedded directly rather than escaped.
    Returns True on success, False on failure (exception is logged).
    """
    from django.utils.html import strip_tags as _strip_tags

    sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    safe_school = escape(school_name)

    if is_html:
        text_body = f"{_strip_tags(message)}\n\n\u2014 {school_name}"
        html_body = (
            "<div style=\"font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;\">"
            f"<div style=\"font-size:15px;line-height:1.6;\">{message}</div>"
            "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:20px 0;\">"
            f"<p style=\"font-size:13px;color:#64748b;\">{safe_school}</p>"
            "</div>"
        )
    else:
        safe_message = escape(message)
        text_body = f"{message}\n\n\u2014 {school_name}"
        html_body = (
            "<div style=\"font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;\">"
            f"<p style=\"font-size:15px;line-height:1.6;\">{safe_message}</p>"
            "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:20px 0;\">"
            f"<p style=\"font-size:13px;color:#64748b;\">{safe_school}</p>"
            "</div>"
        )

    try:
        conn = get_school_email_connection(school)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=sender,
            to=[to_email],
            connection=conn,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send admin message to %s", to_email)
        return False


# ── Lead intake notifications ─────────────────────────────────────────────────

def send_lead_admin_notification(
    *, school, lead, config_raw: Dict[str, Any], lead_cfg: Dict[str, Any] | None = None
) -> bool:
    """
    Notify the school admin when a new lead arrives via the public form or webhook.
    Recipient: lead_cfg.notify_to → leads.notify_to → submission_email.to → skipped.

    lead_cfg: pre-parsed config dict from get_lead_form_config(). When provided,
    used directly so named-variant context (form_title, notify_to) is included.
    Never raises — caller must not let this block lead creation.
    """
    raw = config_raw or {}
    cfg = lead_cfg or {}

    notify_to = (cfg.get("notify_to") or "").strip()
    if not notify_to:
        notify_to = ((raw.get("leads") or {}).get("notify_to") or "").strip()
    if not notify_to:
        notify_to = _get_nested(raw, ["success", "notifications", "submission_email", "to"], "") or ""
        notify_to = notify_to.strip()
    if not notify_to:
        return False

    form_title = (cfg.get("form_title") or "").strip()
    category = (cfg.get("category") or "lead").strip()
    from_email = _resolve_from_email(raw)
    school_name = escape(school.display_name or school.slug)
    program = escape(lead.interested_in_label or "")
    admin_path = f"/schools/{school.slug}/admin/leads/{lead.id}/"

    # Subject reflects the form type so Emily knows what she's looking at
    if form_title and form_title != "Request Information":
        subject = f"{form_title}: {lead.name}"
    else:
        subject = f"New inquiry: {lead.name}" + (f" — {lead.interested_in_label}" if lead.interested_in_label else "")

    form_line = f"Form: {escape(form_title)} [{escape(lead.form_key)}]" if lead.form_key else ""
    category_line = f"Category: {escape(category)}" if category != "lead" else ""

    text_body = "\n".join(filter(None, [
        form_title or "New lead received",
        f"Name: {lead.name}",
        f"Email: {lead.email}",
        f"Phone: {lead.phone}" if lead.phone else "",
        f"Program: {lead.interested_in_label}" if lead.interested_in_label else "",
        form_line,
        category_line,
        f"Source: {lead.source}",
        "",
        f"View lead: {admin_path}",
        "",
        "— Pontora",
    ]))
    html_body = f"""
    <p><strong>{escape(form_title) if form_title else "New lead received"}</strong></p>
    <p>
      <strong>Name:</strong> {escape(lead.name)}<br/>
      <strong>Email:</strong> {escape(lead.email)}<br/>
      {"<strong>Phone:</strong> " + escape(lead.phone) + "<br/>" if lead.phone else ""}
      {"<strong>Program:</strong> " + program + "<br/>" if program else ""}
      {"<strong>Form:</strong> " + escape(form_title) + " [" + escape(lead.form_key) + "]<br/>" if lead.form_key else ""}
      {"<strong>Category:</strong> " + escape(category) + "<br/>" if category != "lead" else ""}
      <strong>Source:</strong> {escape(lead.source)}<br/>
    </p>
    <p>
      <a href="{admin_path}"
         style="display:inline-block;padding:8px 14px;background:#2563eb;color:#fff;
                text-decoration:none;border-radius:6px;font-weight:600;">
        View in admin
      </a>
    </p>
    <hr/><p style="color:#666;font-size:12px;">Pontora &mdash; {school_name}</p>
    """
    try:
        conn = get_school_email_connection(school)
        msg = EmailMultiAlternatives(subject, text_body, from_email, [notify_to], connection=conn)
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Non-blocking: lead admin notification failed for lead %s", lead.pk)
        return False


def send_lead_confirmation(
    *, lead, school_name: str, config_raw: Dict[str, Any], school=None,
    lead_cfg: Dict[str, Any] | None = None,
) -> bool:
    """
    Send a confirmation email to the lead contact.
    Skipped if confirmation_enabled is false or lead has no email.

    lead_cfg: pre-parsed config dict. When provided, confirmation_enabled,
    confirmation_subject, and success_message are read from it directly so
    named variants can configure these independently.
    Never raises — caller must not let this block lead creation.
    """
    if not lead.email:
        return False
    raw = config_raw or {}
    cfg = lead_cfg or (raw.get("leads") or {})

    if not cfg.get("confirmation_enabled", True):
        return False

    from_email = _resolve_from_email(raw)
    success_message = (cfg.get("success_message") or "").strip() or f"Thanks for your interest in {school_name}! We'll follow up soon."
    subject = (cfg.get("confirmation_subject") or "").strip() or f"We received your request — {school_name}"

    text_body = f"Hi {lead.name},\n\n{success_message}\n\n— {school_name}"
    html_body = f"""
    <p>Hi {escape(lead.name)},</p>
    <p>{escape(success_message)}</p>
    <p>— {escape(school_name)}</p>
    <hr/><p style="color:#666;font-size:12px;">Pontora</p>
    """
    try:
        conn = get_school_email_connection(school)
        msg = EmailMultiAlternatives(subject, text_body, from_email, [lead.email], connection=conn)
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Non-blocking: lead confirmation failed for lead %s", lead.pk)
        return False
