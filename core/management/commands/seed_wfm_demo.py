"""
Set up World Famed Masters of Music Academy demo: school, programs, admin user, leads, and submissions.

Usage:
    python manage.py seed_wfm_demo
    python manage.py seed_wfm_demo --force   # re-seed submissions even if data exists

Idempotent: school/programs/user are get_or_created; submissions skipped if >= 5 exist
unless --force is passed.

Programs:
  Open enrollment: Wind Instruments, Drums, Piano, Voice, Music Theory, Sight Singing, Arranging & Composing
  Ensemble (placement): Band
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone as tz

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Lead, School, SchoolAdminMembership, SchoolProgram, Submission

User = get_user_model()

SCHOOL_SLUG = "world-famed-masters"
ADMIN_USERNAME = "wfm_admin"
ADMIN_PASSWORD = "WfmAdmin@123"

# (name, code, auto_enroll, waitlist_enabled)
PROGRAMS = [
    ("Wind Instruments", "wind_instruments", True, False),
    ("Drums & Percussion", "drums", True, False),
    ("Piano", "piano", True, True),
    ("Band Ensemble", "band", False, True),
    ("Voice", "voice", True, False),
    ("Music Theory", "music_theory", True, False),
    ("Sight Singing", "sight_singing", True, False),
    ("Arranging & Composing", "arranging_composing", True, False),
]

# Realistic Inglewood/South LA area names
STUDENTS = [
    ("Marcus", "Williams", "m"),
    ("Jaylen", "Brown", "m"),
    ("Destiny", "Johnson", "f"),
    ("Isaiah", "Davis", "m"),
    ("Aaliyah", "Robinson", "f"),
    ("Darius", "Anderson", "m"),
    ("Kayla", "Thomas", "f"),
    ("Jordan", "Jackson", "m"),
    ("Jasmine", "White", "f"),
    ("Elijah", "Harris", "m"),
    ("Amara", "Martin", "f"),
    ("Malik", "Thompson", "m"),
    ("Brianna", "Garcia", "f"),
    ("Devon", "Martinez", "m"),
    ("Simone", "Lewis", "f"),
    ("Tre", "Walker", "m"),
    ("Naomi", "Hall", "f"),
    ("Andre", "Allen", "m"),
    ("Zoe", "Young", "f"),
    ("Kendall", "Hernandez", "f"),
    ("Tyrese", "King", "m"),
    ("Alicia", "Wright", "f"),
    ("Cameron", "Scott", "m"),
    ("Imani", "Green", "f"),
    ("Quincy", "Adams", "m"),
    ("Tiffany", "Baker", "f"),
    ("Devin", "Nelson", "m"),
    ("Kiana", "Carter", "f"),
    ("Jalen", "Mitchell", "m"),
    ("Nia", "Perez", "f"),
]

GUARDIAN_FIRST = [
    "Tamika", "Denise", "Patricia", "LaShonda", "Keisha", "Angela",
    "Renee", "Tanya", "Vanessa", "Monica", "Robert", "Marcus",
    "Darnell", "Kevin", "Anthony", "Michael", "James", "Derrick",
]

NOTES = [
    "Student has been playing trumpet informally for two years — ready to join the ensemble.",
    "Single parent, needs evening scheduling. Flexible on days.",
    "Sibling attends WFM already — hoping to join the same rehearsal day.",
    "Found WFM through the Inglewood parade performance — very inspired.",
    "College-bound student interested in scholarship preparation pathway.",
    "Student was in their school band but wants more advanced training.",
    "Family just relocated to Inglewood from Atlanta — excited about the program.",
    "Parent asked about instrument rental — student doesn't own one yet.",
    "",
    "",
    "",
    "",
]

# (days_ago_min, days_ago_max, status, program_code, count)
BATCHES = [
    # Previous period (31–60 days ago) — fills comparison column in reports
    (50, 60, "Enrolled",            "band",              3),
    (46, 58, "Enrolled",            "wind_instruments",  2),
    (42, 55, "Enrolled",            "drums",             2),
    (38, 52, "Audition Completed",  "band",              2),
    (34, 50, "Contacted",           "piano",             2),
    (31, 46, "Archived",            "voice",             1),
    (31, 44, "New",                 "music_theory",      2),
    # Current period (0–30 days) — default reports view
    (24, 30, "Enrolled",            "band",              3),
    (22, 29, "Enrolled",            "wind_instruments",  2),
    (20, 28, "Enrolled",            "drums",             2),
    (18, 26, "Enrolled",            "piano",             1),
    (16, 26, "Audition Scheduled",  "band",              3),
    (14, 24, "Audition Scheduled",  "band",              2),
    (12, 22, "In Review",           "voice",             2),
    (10, 20, "In Review",           "wind_instruments",  2),
    ( 8, 18, "Contacted",           "music_theory",      2),
    ( 6, 14, "Needs Follow Up",     "band",              2),
    ( 5, 12, "Needs Follow Up",     "sight_singing",     1),
    ( 4, 10, "Waitlisted",          "piano",             2),
    ( 4,  9, "Waitlisted",          "band",              2),
    ( 3,  8, "New",                 "arranging_composing", 2),
    ( 2,  7, "New",                 "voice",             3),
    ( 1,  5, "New",                 "wind_instruments",  3),
    ( 0,  3, "New",                 "band",              3),
]

LEADS = [
    ("Tanisha",  "Moore",    "community_event", "tanisha.moore@gmail.com",    "(323) 555-0182"),
    ("Deondre",  "Watkins",  "social_media",    "d.watkins@icloud.com",       "(310) 555-0247"),
    ("Gloria",   "Reyes",    "referral",        "gloria.reyes@gmail.com",     "(323) 555-0391"),
    ("Terrence", "Hill",     "school",          "t.hill@outlook.com",         "(424) 555-0456"),
    ("Latoya",   "Jenkins",  "referral",        "ljenkins@gmail.com",         "(310) 555-0518"),
    ("Brandon",  "Coleman",  "google",          "brandon.c@yahoo.com",        "(818) 555-0634"),
]


class Command(BaseCommand):
    help = "Set up World Famed Masters of Music Academy demo school, programs, admin user, and sample data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed submissions even if they already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        rng = random.Random(42)

        # ── School ───────────────────────────────────────────────────────────
        school, created = School.objects.get_or_create(
            slug=SCHOOL_SLUG,
            defaults={
                "display_name": "World Famed Masters of Music Academy",
                "plan": "trial",
                "is_active": True,
            },
        )
        if not school.is_active:
            school.is_active = True
            school.save(update_fields=["is_active"])
        if school.plan != "trial":
            school.plan = "trial"
            school.save(update_fields=["plan"])

        from django.utils import timezone as dtz
        school.trial_started_at = dtz.now() - timedelta(days=3)
        school.save(update_fields=["trial_started_at"])

        if school.program_field_key != "program_interest":
            school.program_field_key = "program_interest"
            school.save(update_fields=["program_field_key"])

        if not school.is_demo:
            school.is_demo = True
            school.save(update_fields=["is_demo"])

        if not school.activity_tracking_enabled:
            school.activity_tracking_enabled = True
            school.save(update_fields=["activity_tracking_enabled"])

        self.stdout.write(f"  {'Created' if created else 'Exists'}: school {school.slug}")

        # ── Programs ─────────────────────────────────────────────────────────
        program_map: dict[str, SchoolProgram] = {}
        for name, code, auto_enroll, waitlist in PROGRAMS:
            prog, prog_created = SchoolProgram.objects.get_or_create(
                school=school,
                code=code,
                defaults={
                    "name": name,
                    "is_active": True,
                    "auto_enroll": auto_enroll,
                    "waitlist_enabled": waitlist,
                },
            )
            if not prog_created:
                prog.auto_enroll = auto_enroll
                prog.waitlist_enabled = waitlist
                prog.save(update_fields=["auto_enroll", "waitlist_enabled"])
            program_map[code] = prog
            label = "auto-enroll" if auto_enroll else "placement req'd"
            verb = "Created" if prog_created else "Exists"
            self.stdout.write(f"  {verb}: {code} ({label})")

        # ── Admin user ───────────────────────────────────────────────────────
        user, user_created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={
                "email": "admin@worldfamedmasters.com",
                "is_staff": True,
                "is_superuser": False,
                "is_active": True,
            },
        )
        user.set_password(ADMIN_PASSWORD)
        if not user.is_staff:
            user.is_staff = True
        user.save(update_fields=["password", "is_staff"] if not user_created else None)
        SchoolAdminMembership.objects.get_or_create(user=user, school=school)
        self.stdout.write(f"  {'Created' if user_created else 'Exists'}: user {ADMIN_USERNAME}")

        # ── Submissions ──────────────────────────────────────────────────────
        existing = Submission.objects.filter(school=school).count()
        if existing >= 5 and not opts["force"]:
            self.stdout.write(
                f"  Skipping submissions — {existing} already exist. Use --force to re-seed."
            )
        else:
            if existing:
                Submission.objects.filter(school=school).delete()

            now = datetime.now(tz=tz.utc)
            student_pool = list(STUDENTS)
            rng.shuffle(student_pool)
            student_idx = 0
            count = 0

            for days_min, days_max, status, prog_code, n in BATCHES:
                prog = program_map.get(prog_code)
                for _ in range(n):
                    first, last, gender = student_pool[student_idx % len(student_pool)]
                    student_idx += 1

                    age_years = rng.randint(8, 22)
                    dob = now.date() - timedelta(days=age_years * 365 + rng.randint(0, 364))

                    g_first = rng.choice(GUARDIAN_FIRST)
                    g_email = f"{g_first.lower()}.{last.lower()}@example.com"
                    area = rng.choice(["310", "424", "323", "213"])
                    g_phone = f"({area}) 555-{rng.randint(1000, 9999)}"

                    days = ["mon", "tue", "wed", "thu", "fri", "sat"]
                    preferred_days = rng.sample(days, k=rng.randint(1, 3))

                    sub = Submission.objects.create(
                        school=school,
                        program=prog,
                        status=status,
                        data={
                            "student_first_name": first,
                            "student_last_name": last,
                            "date_of_birth": dob.isoformat(),
                            "gender": gender,
                            "program_interest": f"program:{prog_code}",
                            "experience_level": rng.choice(["none", "beginner", "intermediate", "advanced"]),
                            "owns_instrument": rng.choice(["yes", "no_rental", "no_purchase", "not_sure"]),
                            "preferred_days": preferred_days,
                            "preferred_time": rng.choice(["morning", "afternoon", "evening", "flexible"]),
                            "guardian_name": f"{g_first} {last}",
                            "guardian_email": g_email,
                            "guardian_phone": g_phone,
                            "relationship": rng.choice(["mother", "father", "guardian"]),
                            "how_did_you_hear": rng.choice(["referral", "social_media", "community_event", "school", "google"]),
                            "notes": rng.choice(NOTES),
                        },
                    )
                    days_ago = rng.randint(days_min, days_max)
                    back_dated = now - timedelta(days=days_ago, hours=rng.randint(0, 10))
                    update_fields = {"created_at": back_dated}
                    if status == "Needs Follow Up":
                        update_fields["next_follow_up_at"] = now - timedelta(days=rng.randint(1, 5))
                    Submission.objects.filter(pk=sub.pk).update(**update_fields)
                    count += 1

            self.stdout.write(f"  Created {count} submissions.")

        # ── Leads ────────────────────────────────────────────────────────────
        existing_leads = Lead.objects.filter(school=school).count()
        if existing_leads >= 3 and not opts["force"]:
            self.stdout.write(f"  Skipping leads — {existing_leads} already exist.")
        else:
            if existing_leads:
                Lead.objects.filter(school=school).delete()

            now_dt = timezone.now()
            lead_statuses = ["new", "contacted", "contacted", "trial_scheduled", "enrolled", "lost"]
            rng.shuffle(lead_statuses)

            enrolled_subs = list(
                Submission.objects.filter(school=school, status="Enrolled").order_by("-created_at")[:2]
            )

            for i, (first, last, source, email, phone) in enumerate(LEADS):
                status = lead_statuses[i]
                converted_sub = None
                converted_at = None
                if status == "enrolled" and enrolled_subs:
                    converted_sub = enrolled_subs.pop(0)
                    converted_at = now_dt - timedelta(days=rng.randint(5, 20))

                lead = Lead.objects.create(
                    school=school,
                    name=f"{first} {last}",
                    email=email,
                    phone=phone,
                    source=source,
                    status=status,
                    converted_submission=converted_sub,
                    converted_at=converted_at,
                )
                days_ago = rng.randint(5, 45)
                Lead.objects.filter(pk=lead.pk).update(
                    created_at=now_dt - timedelta(days=days_ago)
                )

            leads_created = Lead.objects.filter(school=school).count()
            self.stdout.write(f"  Created {leads_created} leads.")

        from django.conf import settings
        demo_base = getattr(settings, "DEMO_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Done."
            f"\n  Admin:   {demo_base}/schools/{SCHOOL_SLUG}/admin/"
            f"\n  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
            f"\n  Demo:    {demo_base}/demo/wfm-demo/"
            f"\n  Form:    {demo_base}/schools/{SCHOOL_SLUG}/apply/"
        ))
