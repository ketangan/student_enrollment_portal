"""
Customer onboarding logic: demo-to-customer conversion, checklist management, welcome email.
"""
from __future__ import annotations

import io
import os
import re
import base64
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from core.models import (
    AdminAuditLog,
    DemoAccessToken,
    DemoArchive,
    Lead,
    OnboardingChecklistItem,
    School,
    SchoolAdminMembership,
    Submission,
)


# ── Archive ───────────────────────────────────────────────────────────────────

def _serialize_rows(rows):
    """Convert queryset .values() rows to JSON-safe dicts."""
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            clean[k] = v.isoformat() if hasattr(v, "isoformat") else v
        result.append(clean)
    return result


def _get_school_config_yaml(school: School) -> str:
    from django.conf import settings
    path = os.path.join(settings.BASE_DIR, "configs", "schools", f"{school.slug}.yaml")
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def archive_demo_data(school: School, actor: User) -> DemoArchive:
    """Snapshot demo submissions/leads/config before conversion. Safe to call multiple times."""
    subs = _serialize_rows(Submission.objects.filter(school=school).values())
    leads = _serialize_rows(Lead.objects.filter(school=school).values())
    config_yaml = _get_school_config_yaml(school)

    archive, _ = DemoArchive.objects.update_or_create(
        school=school,
        defaults={
            "archived_by": actor,
            "submissions_json": subs,
            "leads_json": leads,
            "config_yaml": config_yaml,
        },
    )
    return archive


# ── Conversion ────────────────────────────────────────────────────────────────

def convert_demo_to_customer(
    *,
    school: School,
    plan: str,
    trial_days: int | None,
    admin_email: str,
    admin_first_name: str,
    admin_last_name: str,
    delete_submissions: bool,
    delete_leads: bool,
    actor: User,
) -> dict:
    """
    Convert a demo school to a paying customer.

    Returns:
        dict with keys: user, magic_token, deleted_submissions, deleted_leads, user_created
    """
    with transaction.atomic():
        now = timezone.now()

        # 1. Archive first — rollback insurance before any mutation
        archive_demo_data(school, actor)

        # 2. Optionally remove demo data
        deleted_submissions = 0
        deleted_leads = 0
        if delete_submissions:
            deleted_submissions = Submission.objects.filter(school=school).count()
            Submission.objects.filter(school=school).delete()
        if delete_leads:
            deleted_leads = Lead.objects.filter(school=school).count()
            Lead.objects.filter(school=school).delete()

        # 3. Expire all demo-purpose tokens (leave onboarding tokens untouched)
        DemoAccessToken.objects.filter(
            school=school, purpose=DemoAccessToken.PURPOSE_DEMO
        ).update(expires_at=now - timedelta(seconds=1))

        # 4. Remove ALL existing admin memberships (demo users lose access)
        #    User accounts are preserved for audit history.
        school.admin_memberships.all().delete()

        # 5. Create or update the real admin user
        username = _derive_username(admin_email)
        try:
            user = User.objects.get(email__iexact=admin_email)
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
            user_created = False
        except User.DoesNotExist:
            user = User.objects.create_user(
                username=username,
                email=admin_email,
                first_name=admin_first_name or "",
                last_name=admin_last_name or "",
                is_staff=True,
                is_active=True,
            )
            user_created = True

        # Assign membership — replace any previous school assignment
        try:
            membership = SchoolAdminMembership.objects.get(user=user)
            membership.school = school
            membership.save(update_fields=["school"])
        except SchoolAdminMembership.DoesNotExist:
            SchoolAdminMembership.objects.create(user=user, school=school)

        # 6. Update the school record
        school.is_demo = False
        school.plan = plan
        school.is_active = True
        if plan == "trial":
            school.trial_started_at = now
            if trial_days:
                school.trial_end_date = (now + timedelta(days=int(trial_days))).date()
        school.save()

        # 7. Create onboarding magic link (app domain, no demo banner)
        magic_token = DemoAccessToken.objects.create(
            school=school,
            expires_at=now + timedelta(days=7),
            created_by=actor,
            purpose=DemoAccessToken.PURPOSE_ONBOARDING,
        )

        # 8. Auto-complete first checklist items
        _mark_item(school, "school_created", actor, now)
        _mark_item(school, "plan_configured", actor, now)
        if plan == "trial":
            _mark_item(school, "trial_configured", actor, now)
        _mark_item(school, "admin_invited", actor, now)

        # 9. Audit
        AdminAuditLog.objects.create(
            actor=actor,
            action="action",
            model_label="core.school",
            object_id=str(school.pk),
            object_repr=school.slug,
            extra={
                "name": "demo_converted_to_customer",
                "school_slug": school.slug,
                "plan": plan,
                "trial_end": school.trial_end_date.isoformat() if school.trial_end_date else None,
                "admin_emails": [admin_email],
                "removed_demo_submissions_count": deleted_submissions,
                "removed_demo_leads_count": deleted_leads,
                "token_purpose": DemoAccessToken.PURPOSE_ONBOARDING,
            },
        )

        return {
            "user": user,
            "user_created": user_created,
            "magic_token": magic_token,
            "deleted_submissions": deleted_submissions,
            "deleted_leads": deleted_leads,
        }


def _derive_username(email: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "_", email.split("@")[0].lower())[:28]
    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}_{counter}"
        counter += 1
    return username


# ── Checklist ─────────────────────────────────────────────────────────────────

def get_or_create_checklist(school: School) -> list[OnboardingChecklistItem]:
    """Ensure all 15 fixed checklist items exist for the school. Returns ordered list."""
    existing_keys = set(
        OnboardingChecklistItem.objects.filter(school=school).values_list("item", flat=True)
    )
    to_create = [
        OnboardingChecklistItem(school=school, item=key)
        for key, _ in OnboardingChecklistItem.ITEMS
        if key not in existing_keys
    ]
    if to_create:
        OnboardingChecklistItem.objects.bulk_create(to_create, ignore_conflicts=True)

    by_key = {
        obj.item: obj
        for obj in OnboardingChecklistItem.objects.filter(school=school).select_related("completed_by")
    }
    return [by_key[key] for key, _ in OnboardingChecklistItem.ITEMS if key in by_key]


def mark_checklist_item(school: School, item_key: str, actor: User) -> None:
    obj, _ = OnboardingChecklistItem.objects.get_or_create(school=school, item=item_key)
    if not obj.completed_at:
        obj.completed_at = timezone.now()
        obj.completed_by = actor
        obj.save(update_fields=["completed_at", "completed_by"])
        AdminAuditLog.objects.create(
            actor=actor,
            action="action",
            model_label="core.onboardingchecklistitem",
            object_id=str(obj.pk),
            object_repr=f"{school.slug} — {item_key}",
            extra={"name": "onboarding_checklist_item_completed", "school_slug": school.slug, "item": item_key},
        )


def unmark_checklist_item(school: School, item_key: str, actor: User) -> None:
    updated = OnboardingChecklistItem.objects.filter(school=school, item=item_key).update(
        completed_at=None, completed_by=None
    )
    if updated:
        AdminAuditLog.objects.create(
            actor=actor,
            action="action",
            model_label="core.onboardingchecklistitem",
            object_id="",
            object_repr=f"{school.slug} — {item_key}",
            extra={"name": "onboarding_checklist_item_reopened", "school_slug": school.slug, "item": item_key},
        )


def _mark_item(school: School, item_key: str, actor: User, now) -> None:
    """Internal helper: mark an item only if not already completed. No audit log."""
    obj, _ = OnboardingChecklistItem.objects.get_or_create(school=school, item=item_key)
    if not obj.completed_at:
        obj.completed_at = now
        obj.completed_by = actor
        obj.save(update_fields=["completed_at", "completed_by"])


# ── QR Code ───────────────────────────────────────────────────────────────────

def qr_base64(url: str) -> str:
    """Generate a base64-encoded PNG QR code for the given URL."""
    try:
        import segno
        qr = segno.make(url, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=5)
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return ""


# ── Welcome Email ──────────────────────────────────────────────────────────────

def send_welcome_email(school: School, actor: User) -> bool:
    """
    Send a one-click customer welcome email to the school's first admin.
    Creates a fresh 7-day onboarding token for the magic link.
    Returns True on success.
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from core.services.url_builder import app_reverse, app_url

    membership = school.admin_memberships.select_related("user").first()
    if not membership or not membership.user.email:
        return False

    admin = membership.user
    school_name = school.display_name or school.slug
    now = timezone.now()

    magic_token = DemoAccessToken.objects.create(
        school=school,
        expires_at=now + timedelta(days=7),
        created_by=actor,
        purpose=DemoAccessToken.PURPOSE_ONBOARDING,
    )

    login_url = app_url("/login/")
    magic_link = app_reverse("demo_access", kwargs={"token": magic_token.token})
    enrollment_url = app_reverse("apply", kwargs={"school_slug": school.slug})
    iframe_snippet = (
        f'<iframe src="{enrollment_url}" width="100%" height="800" '
        f'frameborder="0" style="border:none;"></iframe>'
    )
    qr_img = qr_base64(enrollment_url)

    subject = f"Welcome to Pontora — {school_name} is ready!"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@mypontora.com")

    text_body = _welcome_text(
        admin=admin, school_name=school_name,
        login_url=login_url, magic_link=magic_link,
        enrollment_url=enrollment_url, iframe_snippet=iframe_snippet,
    )
    html_body = _welcome_html(
        admin=admin, school_name=school_name,
        login_url=login_url, magic_link=magic_link,
        enrollment_url=enrollment_url, iframe_snippet=iframe_snippet,
        qr_img=qr_img,
        trial_end=school.trial_end_date,
    )

    msg = EmailMultiAlternatives(subject, text_body, from_email, [admin.email])
    msg.attach_alternative(html_body, "text/html")
    try:
        msg.send()
    except Exception:
        return False

    AdminAuditLog.objects.create(
        actor=actor,
        action="action",
        model_label="core.school",
        object_id=str(school.pk),
        object_repr=school.slug,
        extra={
            "name": "customer_welcome_email_sent",
            "school_slug": school.slug,
            "to": admin.email,
        },
    )
    return True


def _welcome_text(*, admin, school_name, login_url, magic_link, enrollment_url, iframe_snippet):
    first = admin.first_name or "there"
    return f"""Hi {first},

{school_name} is now live on Pontora. Here's everything you need.

────────────────────────────────────────────
QUICK START
────────────────────────────────────────────

Admin Portal:   {login_url}
Magic Link:     {magic_link}
  (One-click sign-in — valid 7 days)

Enrollment Form: {enrollment_url}

────────────────────────────────────────────
EMBED ON YOUR WEBSITE
────────────────────────────────────────────

{iframe_snippet}

────────────────────────────────────────────
NEXT STEPS
────────────────────────────────────────────

1. Use the magic link above to sign in.
2. Set your password: top-right menu → Change Password.
3. Share your enrollment form link on your website.
4. When families submit, review them in your admin dashboard.

Questions? Reply to this email anytime.

— Ketan at Pontora
"""


def _welcome_html(*, admin, school_name, login_url, magic_link, enrollment_url, iframe_snippet, qr_img, trial_end):
    first = admin.first_name or "there"
    trial_row = ""
    if trial_end:
        trial_row = f"""
      <tr><td style="padding:5px 0;color:#6b7280;width:140px;">Trial ends</td>
          <td style="font-weight:600;">{trial_end.strftime("%B %d, %Y")}</td></tr>"""
    qr_section = ""
    if qr_img:
        qr_section = f"""
  <div style="border:1px solid #e5e7eb;border-radius:8px;padding:18px 20px;margin-bottom:20px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;margin-bottom:10px;">QR Code — Print or Share</div>
    <p style="font-size:13px;color:#4b5563;margin:0 0 10px;">Scan to open the enrollment form. Great for flyers and lobby displays.</p>
    <img src="data:image/png;base64,{qr_img}" width="160" height="160" alt="Enrollment form QR code" style="display:block;">
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Welcome to Pontora</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#111;">

  <div style="margin-bottom:24px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6b7280;">Pontora</div>
    <h1 style="font-size:22px;font-weight:700;margin:8px 0 4px;">{school_name} is live!</h1>
    <p style="font-size:14px;color:#4b5563;margin:0;">Hi {first}, your school is ready. Here&rsquo;s everything you need.</p>
  </div>

  <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:18px 20px;margin-bottom:20px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#166534;margin-bottom:10px;">Quick Access — valid 7 days</div>
    <a href="{magic_link}" style="display:inline-block;background:#16a34a;color:#fff;font-weight:600;font-size:14px;padding:10px 20px;border-radius:6px;text-decoration:none;">Sign in to your admin portal &rarr;</a>
    <div style="font-size:11px;color:#6b7280;margin-top:8px;">Or paste: <a href="{magic_link}" style="color:#2563eb;word-break:break-all;">{magic_link}</a></div>
  </div>

  <div style="border:1px solid #e5e7eb;border-radius:8px;padding:18px 20px;margin-bottom:20px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;margin-bottom:14px;">Your Links</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:5px 0;color:#6b7280;width:140px;">Admin login</td>
          <td><a href="{login_url}" style="color:#2563eb;">{login_url}</a></td></tr>
      <tr><td style="padding:5px 0;color:#6b7280;">Enrollment form</td>
          <td><a href="{enrollment_url}" style="color:#2563eb;">{enrollment_url}</a></td></tr>{trial_row}
    </table>
  </div>

  <div style="border:1px solid #e5e7eb;border-radius:8px;padding:18px 20px;margin-bottom:20px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;margin-bottom:10px;">Embed on Your Website</div>
    <p style="font-size:13px;color:#4b5563;margin:0 0 10px;">Paste this HTML to embed the form directly on your site:</p>
    <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;padding:12px;font-family:monospace;font-size:11px;color:#374151;word-break:break-all;">{iframe_snippet}</div>
  </div>

{qr_section}

  <div style="border:1px solid #e5e7eb;border-radius:8px;padding:18px 20px;margin-bottom:20px;">
    <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;margin-bottom:10px;">Getting Started</div>
    <ol style="font-size:13px;color:#374151;margin:0;padding-left:20px;line-height:1.8;">
      <li>Click the magic link above to sign in.</li>
      <li>Set your password: top-right menu &rarr; <strong>Change Password</strong>.</li>
      <li>Share your enrollment form link on your website and social.</li>
      <li>Review submitted applications from your admin dashboard.</li>
    </ol>
  </div>

  <div style="font-size:12px;color:#9ca3af;margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;">
    Questions? Reply to this email anytime.<br>
    <strong style="color:#374151;">Ketan at Pontora</strong>
  </div>

</body>
</html>"""
