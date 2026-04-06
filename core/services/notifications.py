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


def _find_applicant_email(submission_data: dict, config_raw: dict) -> Optional[str]:
    """
    Scans all form sections (single-form and multi-form) for fields of type=email.
    Prefers required fields over optional. Returns the first non-empty value found
    in submission_data, or None if no email field/value exists.
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

    data = submission_data or {}
    for key in required_keys + optional_keys:
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
    lines += ["", f"— {school_name}"]
    text_body = "\n".join(lines)

    # HTML
    greeting = f"Hi {escape(student_name)}," if student_name else "Hi,"
    response_line = (
        f"<p><strong>Expected response time:</strong> {escape(response_time)}</p>"
        if response_time
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
    