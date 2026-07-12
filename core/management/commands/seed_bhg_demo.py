"""
Set up Beverly Hills Gymnastics Center demo: school, programs, admin user, leads, and submissions.

Usage:
    python manage.py seed_bhg_demo
    python manage.py seed_bhg_demo --force   # re-seed submissions even if data exists

Idempotent: school/programs/user are get_or_created; submissions skipped if >= 5 exist
unless --force is passed.

Programs:
  Auto-enroll (no placement test): Mini-Tots, Tumbling, Adult Gymnastics, Private Lessons
  Placement required: Girls Beginning/Intermediate/Advanced, Boys Beginning, Rhythmic, Competition Pre-Team
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

SCHOOL_SLUG = "beverly-hills-gymnastics"
ADMIN_USERNAME = "bhg_demo"
ADMIN_PASSWORD = "DemoAccess@123"

# (name, code, auto_enroll, waitlist_enabled)
PROGRAMS = [
    # ── Open enrollment (no placement required) ──
    ("Mini-Tots (Ages 3–4)", "mini_tots", True, True),
    ("Tumbling For All Ages", "tumbling", True, False),
    ("Adult Gymnastics", "adult_gymnastics", True, False),
    ("Private Lessons", "private_lessons", True, False),
    # ── Placement required ──
    ("Girls Beginning Gymnastics (Ages 5–7)", "girls_beginning", False, True),
    ("Girls Intermediate Gymnastics (Ages 7–10)", "girls_intermediate", False, True),
    ("Girls Advanced Gymnastics (Ages 10+)", "girls_advanced", False, False),
    ("Boys Beginning Gymnastics (Ages 5–8)", "boys_beginning", False, True),
    ("Rhythmic Gymnastics", "rhythmic", False, False),
    ("Competition Pre-Team — Bronze (Ages 3–7)", "competition_pre_team", False, False),
]

# Realistic LA-area family names for a Beverly Hills gymnastics center
STUDENTS = [
    ("Sophia", "Goldstein", "f"),
    ("Emma", "Nakamura", "f"),
    ("Olivia", "Hernandez", "f"),
    ("Mia", "Cohen", "f"),
    ("Isabella", "Park", "f"),
    ("Ava", "Williams", "f"),
    ("Luna", "Moreau", "f"),
    ("Chloe", "Tanaka", "f"),
    ("Harper", "Ramirez", "f"),
    ("Lily", "Kim", "f"),
    ("Scarlett", "Russo", "f"),
    ("Zoe", "Patel", "f"),
    ("Stella", "Anderson", "f"),
    ("Aria", "Martinez", "f"),
    ("Nora", "Singh", "f"),
    ("Penelope", "Chen", "f"),
    ("Layla", "Johnson", "f"),
    ("Elena", "Okafor", "f"),
    ("Maya", "Ferreira", "f"),
    ("Violet", "Thompson", "f"),
    ("Liam", "Davis", "m"),
    ("Noah", "Garcia", "m"),
    ("Ethan", "Lee", "m"),
    ("Lucas", "Brown", "m"),
    ("Logan", "Wilson", "m"),
    ("Mason", "Taylor", "m"),
    ("Jackson", "Moore", "m"),
    ("Aiden", "Jackson", "m"),
    ("Carter", "White", "m"),
    ("Sebastian", "Harris", "m"),
]

GUARDIAN_FIRST = [
    "Jennifer", "Michelle", "Ashley", "Amanda", "Sarah", "Lauren",
    "Rachel", "Megan", "Stephanie", "Elizabeth", "Jessica", "Kimberly",
    "David", "Michael", "Robert", "James", "Christopher", "Daniel",
]

NOTES = [
    "Has been doing recreational gymnastics at another gym — ready to move up.",
    "Sibling is already enrolled in Girls Intermediate — hoping to join same schedule.",
    "Very flexible on days; mornings preferred due to school pickup.",
    "Referred by the Goldstein family — their daughter loves the program.",
    "Was on a competition team at previous school in New York — just relocated.",
    "Looking for a program with Saturday morning availability.",
    "Parent asked about sibling discount for two gymnasts registering.",
    "Child is shy but very interested — please assign a patient coach.",
    "",
    "",
    "",
    "",
]

# (days_ago_min, days_ago_max, status, program_code, count)
BATCHES = [
    # Previous period (31–60 days ago) — fills comparison column in reports
    (50, 60, "Enrolled",             "girls_beginning",     2),
    (45, 58, "Enrolled",             "mini_tots",           2),
    (40, 55, "Enrolled",             "tumbling",            1),
    (35, 52, "Placement Completed",  "girls_intermediate",  2),
    (31, 48, "Contacted",            "boys_beginning",      2),
    (31, 45, "Archived",             "girls_beginning",     1),
    (31, 44, "New",                  "adult_gymnastics",    2),
    # Current period (0–30 days) — what the default reports view shows
    (24, 30, "Enrolled",             "girls_beginning",     2),
    (20, 28, "Enrolled",             "mini_tots",           2),
    (18, 26, "Enrolled",             "tumbling",            2),
    (15, 25, "Placement Scheduled",  "girls_intermediate",  2),
    (12, 22, "Placement Scheduled",  "competition_pre_team",1),
    (10, 20, "In Review",            "girls_advanced",      2),
    (10, 18, "In Review",            "rhythmic",            1),
    ( 7, 15, "Contacted",            "boys_beginning",      2),
    ( 5, 12, "Needs Follow Up",      "girls_beginning",     2),
    ( 4, 10, "Waitlisted",           "girls_intermediate",  1),
    ( 3,  8, "New",                  "private_lessons",     3),
    ( 2,  7, "New",                  "adult_gymnastics",    2),
    ( 1,  5, "New",                  "girls_beginning",     3),
    ( 0,  3, "New",                  "mini_tots",           2),
]

LEADS = [
    ("Gabrielle", "Laurent",  "social",    "glaurent@gmail.com",       "(310) 555-0182"),
    ("Tyler",     "Reeves",   "referral",  "treeves@icloud.com",       "(424) 555-0247"),
    ("Keiko",     "Watanabe", "website",   "keiko.w@gmail.com",        "(323) 555-0391"),
    ("Marcus",    "Freeman",  "drove_by",  "mfreeman@outlook.com",     "(310) 555-0456"),
    ("Natasha",   "Ivanova",  "referral",  "n.ivanova@gmail.com",      "(818) 555-0518"),
    ("Jordan",    "Ellis",    "google",    "jellis@yahoo.com",         "(626) 555-0634"),
]


class Command(BaseCommand):
    help = "Set up Beverly Hills Gymnastics Center demo school, programs, admin user, and sample data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed submissions even if they already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        rng = random.Random(99)

        # ── School ───────────────────────────────────────────────────────────
        school, created = School.objects.get_or_create(
            slug=SCHOOL_SLUG,
            defaults={
                "display_name": "Beverly Hills Gymnastics Center",
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

        # Reset trial so it doesn't expire during the demo
        from django.utils import timezone as dtz
        school.trial_started_at = dtz.now() - timedelta(days=3)
        school.save(update_fields=["trial_started_at"])

        if school.program_field_key != "interested_in":
            school.program_field_key = "interested_in"
            school.save(update_fields=["program_field_key"])

        if not school.is_demo:
            school.is_demo = True
            school.save(update_fields=["is_demo"])

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
                # Keep auto_enroll/waitlist in sync on re-runs
                prog.auto_enroll = auto_enroll
                prog.waitlist_enabled = waitlist
                prog.save(update_fields=["auto_enroll", "waitlist_enabled"])
            program_map[code] = prog
            label = "auto-enroll" if auto_enroll else "placement req'd"
            verb = "Created" if prog_created else "Exists"
            self.stdout.write(f"  {verb}: {code} ({label})")

        # ── Admin user ───────────────────────────────────────────────────────
        # Rename legacy bhg_admin → bhg_demo on re-runs so the membership is preserved.
        User.objects.filter(username="bhg_admin").update(username=ADMIN_USERNAME)

        user, user_created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={
                "email": "admin@beverlyhillsgymnastics.org",
                "is_staff": True,
                "is_superuser": False,
                "is_active": True,
            },
        )
        # Always sync password so re-runs keep credentials consistent.
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

                    age_years = rng.randint(3, 17)
                    dob = (now.date() - timedelta(days=age_years * 365 + rng.randint(0, 364)))

                    g_first = rng.choice(GUARDIAN_FIRST)
                    g_email = f"{g_first.lower()}.{last.lower()}@example.com"
                    area = rng.choice(["310", "424", "323", "818"])
                    g_phone = f"({area}) 555-{rng.randint(1000, 9999)}"

                    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
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
                            "interested_in": f"program:{prog_code}",
                            "experience_level": rng.choice(["none", "beginner", "intermediate", "advanced"]),
                            "preferred_days": preferred_days,
                            "preferred_time": rng.choice(["morning", "afternoon", "evening", "flexible"]),
                            "guardian_name": f"{g_first} {last}",
                            "guardian_email": g_email,
                            "guardian_phone": g_phone,
                            "relationship": rng.choice(["mother", "father", "guardian"]),
                            "emergency_contact_name": f"Contact for {last}",
                            "emergency_contact_phone": f"({area}) 555-{rng.randint(1000, 9999)}",
                            "has_medical_conditions": rng.choice(["no", "no", "no", "yes"]),
                            "how_did_you_hear": rng.choice(["google", "referral", "social_media", "drove_by", "current_student"]),
                            "notes": rng.choice(NOTES),
                        },
                    )
                    days_ago = rng.randint(days_min, days_max)
                    back_dated = now - timedelta(days=days_ago, hours=rng.randint(0, 10))
                    Submission.objects.filter(pk=sub.pk).update(created_at=back_dated)
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
            lead_statuses = ["new", "contacted", "contacted", "placement_scheduled", "enrolled", "lost"]
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
        base = getattr(settings, "APP_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
        demo_base = getattr(settings, "DEMO_BASE_URL", base).rstrip("/")
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Done."
            f"\n  Admin:   {base}/schools/{SCHOOL_SLUG}/admin/"
            f"\n  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
            f"\n  Demo:    {demo_base}/demo/bhg-demo/"
            f"\n  Form:    {base}/schools/{SCHOOL_SLUG}/apply/"
        ))
