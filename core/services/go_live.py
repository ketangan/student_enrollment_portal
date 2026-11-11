"""Operator launch guidance; existing school configuration determines applicability.

These are manual attestations, not automatic readiness checks or feature switches.
"""
from core.services.config_loader import get_forms, load_school_config
from scheduling.capabilities import has_capability
from scheduling.models import SchedulingProfile


def checklist_steps(school):
    config = load_school_config(school.slug)
    raw = config.raw if config else {}
    profile = SchedulingProfile.objects.filter(school=school, enabled=True).first()
    # An unconfigured school still needs enrollment setup reviewed. A configured
    # booking-only workspace does not need an invented enrollment form.
    enrollment = bool(get_forms(config)) or not profile or config is None
    bookings = bool(profile and any(has_capability(profile, key)
                    for key in ("workshops", "lessons", "visits", "camps", "parties")))
    payments = bool((raw.get("application_fee") or {}).get("enabled") or
                    (profile and has_capability(profile, "reservations")))
    leads = enrollment and school.features.leads_enabled
    reports = enrollment and school.features.reports_enabled
    trial = school.plan == "trial" or school.trial_requires_activation
    no_enrollment = "No enrollment workflow in this booking-only setup." if not enrollment else ""
    no_payments = "No online enrollment fees or reservation payments configured." if not payments else ""
    recovery = "For online payments, verify signed provider webhooks, failed-payment alerts, retry safety and reconciliation in production. For an offline-only setup, verify that no online payment or recovery workflow is enabled."
    if profile and profile.payment_provider == "square" and payments:
        recovery += " Before live Square launch, enable --include-live-recovery in the production maintenance job and verify a successful run. Never enable it in demo."
    return [
        ("Workspace & access", [
            ("school_created", "Create the school record; a demo remains a separate practice workspace.", "", False),
            ("production_workspace_verified", "Create the real school in app's database. Do not convert the hosted demo in place or restore its database into production.", "", True),
            ("capabilities_reviewed", "Apply only approved YAML policies and database settings. Review any real-data import and totals; exclude sample bookings, students, test payments, demo passwords and access links.", "", False),
            ("plan_configured", "Confirm the signed plan, location pricing and billing terms without charging or starting a trial early.", "", False),
            ("trial_configured", "Confirm the agreed trial duration and start rule. If written go-live is required, record it separately only after acceptance.", "No customer trial is configured." if not trial else "", False),
            ("admin_invited", "Invite the actual owner using production access. Do not reuse shared demo credentials.", "", False),
            ("team_access_configured", "Verify owner, staff and location-scoped access, including denied access to other schools.", "", False),
        ]),
        ("Customer experience", [
            ("branding_configured", "Approve the name, logo, colors, contact details and public policies.", "", False),
            ("programs_configured", "Review the enabled programs, classes or services, prices and capacity.", "", False),
            ("workflows_configured", "Review enrollment forms, consent, statuses and follow-up rules.", no_enrollment, False),
            ("lead_capture_configured", "Verify enabled inquiry forms, routing and follow-up; keep external trial booking links correct.", "Lead capture is not enabled in this setup." if not leads else "", False),
            ("scheduling_verified", "Verify locations, time zones, availability, closures, capacity and booking/cancellation rules for enabled booking types. Test booking, changes, cancellation and staff confirmation end to end. Check materials and preparation only where used.", "No scheduling features are enabled." if not bookings else "", False),
            ("email_templates_reviewed", "Approve this school's templates, triggers and generated payment/guest links. Choose school SMTP or Pontora's email service.", "", False),
            ("website_integration_complete", "Use production enrollment or booking links on the customer's website. Check branding, embedding where supported, and phone/tablet navigation.", "", True),
        ]),
        ("Payments & verification", [
            ("payment_configured", "Confirm how fees are collected, including free or offline workflows. For online payments, connect the real account and correct location; never copy live credentials into demo.", "", False),
            ("live_payment_verified", "For online payments, obtain the owner's approval and verify a small real payment, refund, receipt and accurate totals. If a physical terminal is used, pair it to the correct account/location and test it on site; sandbox cannot validate the hardware. For a free or offline-only launch, verify that online checkout is not exposed instead.", no_payments, True),
            ("recovery_verified", recovery, no_payments, True),
            ("test_submission_completed", "Complete the enabled enrollment journey, including errors, consent, fees where used, confirmation and staff review. Keep test evidence out of real customer records.", no_enrollment, False),
            ("email_delivery_verified", "Verify delivery to an approved inbox, sender identity and the correct production links. Do not send test messages to customers.", "", True),
            ("reports_verified", "Reconcile enrollment and lead reports with the records and permissions in this workspace.", "Enrollment reports are not enabled in this setup." if not reports else "", False),
        ]),
        ("Release & launch", [
            ("release_verified", "Pass the full suite and CI, verify the exact commit on demo, then promote that commit to app. Check migrations, backups, health, scoped jobs and rollback. Test desktop, phone and the actual staff tablet; emulation alone is not hardware acceptance.", "", True),
            ("customer_approved", "Record customer acceptance, the cutover time, the authoritative booking system and ownership of any remaining work. Prevent duplicate bookings across the old and new systems.", "", True),
            ("school_marked_live", "Confirm the production school is active, website links are live and staff have access. Record any required go-live notice/trial activation, then check the first real activity and alerts. This checkbox does not activate the school or start billing.", "", True),
        ]),
    ]
