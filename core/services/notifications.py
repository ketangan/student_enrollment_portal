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

def _build_admin_submission_url(*, request, submission_id: int | str) -> str:
    """
    Returns an absolute URL to the admin change page for this submission.
    Works locally + Render because it uses the incoming request host.
    """
    path = reverse("admin:core_submission_change", args=[submission_id])
    return request.build_absolute_uri(path)

def _admin_url_for_submission(
    *, request: Optional[HttpRequest], submission_id: int | str
) -> str:
    if request is None:
        return f"/admin/core/submission/{submission_id}/change/"
    return _build_admin_submission_url(request=request, submission_id=submission_id)


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
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
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

    base_url = getattr(settings, "BASE_URL", "http://localhost:8000").rstrip("/")
    from django.urls import reverse as _reverse
    resume_path = _reverse("apply_resume", args=[school.slug, draft.token])
    resume_url = f"{base_url}{resume_path}"
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
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
        msg = EmailMessage(subject, body, from_email, [draft.email], connection=conn)
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send resume link email to %s", draft.email)
        return False


def send_status_link_email(*, to_email: str, status_url: str, school_name: str) -> bool:
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
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
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
    subject = _build_submission_email_subject(student_name=student_name, program=program)
    text_body, html_body = _build_submission_email_bodies(
        submission_public_id=submission_public_id,
        student_name=student_name,
        program=program,
        admin_url=admin_url,
    )

    try:
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))

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


# ── Phase 12: admin communication helpers ────────────────────────────────────

_WORKFLOW_DEFAULTS: dict[str, dict[str, str]] = {
    "contacted": {
        "subject": "Reaching out \u2014 {{school}}",
        "body": (
            "Hi {{name}},\n\n"
            "Thank you for your interest in {{school}}. We have recently reached out "
            "and look forward to connecting with you.\n\n"
            "\u2014 {{school}}"
        ),
    },
    "follow_up": {
        "subject": "Following up \u2014 {{school}}",
        "body": (
            "Hi {{name}},\n\n"
            "We wanted to follow up on your inquiry at {{school}}. "
            "Please do not hesitate to reach out with any questions.\n\n"
            "\u2014 {{school}}"
        ),
    },
}


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


def get_communication_template(config_raw: dict, template_key: str) -> tuple[str, str]:
    """
    Returns (subject, body) for a workflow notification email.

    Reads communication.templates.{template_key} from config_raw; falls back to
    _WORKFLOW_DEFAULTS if the key or config is absent.
    """
    # For unknown keys fall back to the "contacted" template rather than empty strings,
    # so callers always receive a sendable subject+body pair.
    defaults = _WORKFLOW_DEFAULTS.get(template_key) or _WORKFLOW_DEFAULTS["contacted"]
    if not isinstance(config_raw, dict):
        return defaults["subject"], defaults["body"]
    tmpl_cfg = _get_nested(config_raw, ["communication", "templates", template_key], default={})
    if not isinstance(tmpl_cfg, dict):
        return defaults["subject"], defaults["body"]
    subject = str(tmpl_cfg.get("subject") or defaults["subject"])
    body = str(tmpl_cfg.get("body") or defaults["body"])
    return subject, body


def send_admin_message(
    *,
    to_email: str,
    subject: str,
    message: str,
    school_name: str,
    from_email: str | None = None,
) -> bool:
    """
    Sends a one-off admin-composed message to a family member.
    Returns True on success, False on failure (exception is logged).
    """
    sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    safe_message = escape(message)
    safe_school = escape(school_name)

    text_body = f"{message}\n\n\u2014 {school_name}"
    html_body = (
        "<div style=\"font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;\">"
        f"<p style=\"font-size:15px;line-height:1.6;\">{safe_message}</p>"
        "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:20px 0;\">"
        f"<p style=\"font-size:13px;color:#64748b;\">{safe_school}</p>"
        "</div>"
    )

    try:
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
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


def send_workflow_notification(
    *,
    to_email: str,
    student_name: str,
    school_name: str,
    notification_type: str,
    config_raw: dict,
    from_email: str | None = None,
) -> bool:
    """
    Sends a workflow-triggered notification email ("we reached out" or "follow-up").

    notification_type: "contacted" | "follow_up"
    Returns True on success, False on failure (exception is logged).
    """
    sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    subject_tmpl, body_tmpl = get_communication_template(config_raw, notification_type)

    context = {"name": student_name, "school": school_name}
    subject = _render_template(subject_tmpl, context)
    body = _render_template(body_tmpl, context)

    safe_body = escape(body)
    safe_school = escape(school_name)
    html_body = (
        "<div style=\"font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px;\">"
        f"<p style=\"font-size:15px;line-height:1.6;white-space:pre-line;\">{safe_body}</p>"
        "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:20px 0;\">"
        f"<p style=\"font-size:13px;color:#64748b;\">{safe_school}</p>"
        "</div>"
    )

    try:
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=sender,
            to=[to_email],
            connection=conn,
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception(
            "Failed to send %s workflow notification to %s", notification_type, to_email
        )
        return False
