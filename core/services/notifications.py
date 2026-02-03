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
    student_name: str,
    program: str,
    admin_url: str,
) -> Tuple[str, str]:
    # Plain text (fallback)
    text_body = "\n".join([
        "New submission received",
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


# ----------------------------
# Public API
# ----------------------------

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
    