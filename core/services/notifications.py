# core/services/notifications.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.http import HttpRequest
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

    # If request is missing (eg management command), build a relative link
    if request is None:
        admin_url = f"/admin/core/submission/{submission_id}/change/"
    else:
        admin_url = _build_admin_submission_url(request=request, submission_id=submission_id)

    subject = f"New submission: {student_name}" + (f" ({program})" if program else "")

    body_lines = [
        "New submission received",
        f"Student: {student_name}",
    ]
    if program:
        body_lines.append(f"Program: {program}")
    body_lines += [
        "",
        f"View in admin: {admin_url}",
        "",
        "— student_enrollment_portal",
    ]
    body = "\n".join(body_lines)

    try:
        conn = get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 10))

        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=cfg.from_email,
            to=cfg.to,
            cc=cfg.cc,
            bcc=cfg.bcc,
            connection=conn,
        )
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send submission notification email")
        return False
    