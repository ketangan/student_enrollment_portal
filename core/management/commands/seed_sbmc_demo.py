"""
Set up South Bay Music Conservatory demo: school, programs, admin user, leads, and submissions.

Usage:
    python manage.py seed_sbmc_demo
    python manage.py seed_sbmc_demo --force   # re-seed submissions even if data exists

Idempotent: school/programs/user are get_or_created; submissions skipped if >= 5 exist
unless --force is passed.

Programs (all review-and-approve):
  Piano Lessons, Violin Lessons, Viola Lessons, Cello Lessons
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

SCHOOL_SLUG = "south-bay-music"
ADMIN_USERNAME = "sbmc_admin"
ADMIN_PASSWORD = "SbmcAdmin@123"

# (name, code, auto_enroll, waitlist_enabled)
PROGRAMS = [
    ("Piano Lessons",  "piano",  False, True),
    ("Violin Lessons", "violin", False, True),
    ("Viola Lessons",  "viola",  False, True),
    ("Cello Lessons",  "cello",  False, True),
]

# Torrance/South Bay area names — Japanese-American, Hispanic, general LA
STUDENTS = [
    ("Yuki",      "Tanaka",    "f"),
    ("Aiko",      "Nakamura",  "f"),
    ("Hana",      "Yoshida",   "f"),
    ("Sofia",     "Ramirez",   "f"),
    ("Emma",      "Park",      "f"),
    ("Lily",      "Chen",      "f"),
    ("Mia",       "Hernandez", "f"),
    ("Chloe",     "Kim",       "f"),
    ("Ava",       "Watanabe",  "f"),
    ("Isabella",  "Martinez",  "f"),
    ("Keiko",     "Suzuki",    "f"),
    ("Aria",      "Patel",     "f"),
    ("Nora",      "Williams",  "f"),
    ("Maya",      "Johnson",   "f"),
    ("Ella",      "Thompson",  "f"),
    ("Kai",       "Tanaka",    "m"),
    ("Ethan",     "Park",      "m"),
    ("Lucas",     "Ramirez",   "m"),
    ("Noah",      "Chen",      "m"),
    ("Liam",      "Nakamura",  "m"),
    ("Mateo",     "Garcia",    "m"),
    ("Oliver",    "Kim",       "m"),
    ("Aiden",     "Yoshida",   "m"),
    ("Sebastian", "Lopez",     "m"),
    ("James",     "Patel",     "m"),
    ("Ryan",      "Watanabe",  "m"),
    ("Dylan",     "Martinez",  "m"),
    ("Connor",    "Williams",  "m"),
    ("Tyler",     "Anderson",  "m"),
    ("Caleb",     "Brown",     "m"),
]

GUARDIAN_FIRST = [
    "Jennifer", "Michelle", "Ashley", "Amanda", "Sarah", "Lauren",
    "Rachel", "Megan", "Stephanie", "Elizabeth", "Jessica", "Kimberly",
    "David", "Michael", "Robert", "James", "Christopher", "Daniel",
]

NOTES = [
    "Student has been watching YouTube piano tutorials and is very motivated to start properly.",
    "Family relocating from Japan — student has Suzuki violin background through Book 3.",
    "Parent wants Saturday lesson times due to school schedule during the week.",
    "Transfer student from another studio — would like to continue with same repertoire if possible.",
    "Adult learner, has always wanted to play cello, very flexible on scheduling.",
    "Sibling is already enrolled in violin — hoping for back-to-back lesson slots.",
    "Student tried a rental violin at school and loved it — parents want to pursue private lessons.",
    "Looking for a teacher experienced with adults returning to piano after a long break.",
    "",
    "",
    "",
    "",
]

# (days_ago_min, days_ago_max, status, program_code, count)
BATCHES = [
    # Previous period (31–60 days ago)
    (50, 60, "Enrolled",        "piano",  3),
    (45, 58, "Enrolled",        "violin", 2),
    (40, 55, "Enrolled",        "cello",  1),
    (35, 52, "Trial Completed", "viola",  2),
    (31, 48, "Trial Scheduled", "piano",  2),
    (31, 45, "Archived",        "violin", 1),
    (31, 44, "New",             "piano",  2),
    # Current period (0–30 days)
    (24, 30, "Enrolled",        "piano",  3),
    (20, 28, "Enrolled",        "violin", 2),
    (18, 26, "Enrolled",        "viola",  1),
    (15, 25, "Trial Scheduled", "cello",  2),
    (12, 22, "Trial Scheduled", "piano",  2),
    (10, 20, "Trial Completed", "violin", 2),
    (10, 18, "In Review",       "viola",  1),
    ( 7, 15, "Needs Follow Up", "piano",  2),
    ( 5, 12, "Waitlisted",      "violin", 1),
    ( 4, 10, "Waitlisted",      "viola",  1),
    ( 3,  8, "New",             "cello",  3),
    ( 2,  7, "New",             "piano",  2),
    ( 1,  5, "New",             "violin", 3),
    ( 0,  3, "New",             "piano",  2),
]

LEADS = [
    ("Yumiko", "Hashimoto", "referral", "yumiko.h@gmail.com",  "(424) 555-0182"),
    ("Carlos", "Mendoza",   "google",   "carlos.m@icloud.com", "(310) 555-0247"),
    ("Sarah",  "Mitchell",  "social",   "smitchell@gmail.com", "(424) 555-0391"),
    ("Kevin",  "Park",      "referral", "kpark@outlook.com",   "(310) 555-0456"),
    ("Linda",  "Nguyen",    "website",  "linda.n@gmail.com",   "(424) 555-0518"),
    ("Thomas", "Okafor",    "drove_by", "t.okafor@yahoo.com",  "(310) 555-0634"),
]


class Command(BaseCommand):
    help = "Set up South Bay Music Conservatory demo school, programs, admin user, and sample data."

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
                "display_name": "South Bay Music Conservatory",
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

        if school.program_field_key != "instrument":
            school.program_field_key = "instrument"
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
                prog.auto_enroll = auto_enroll
                prog.waitlist_enabled = waitlist
                prog.save(update_fields=["auto_enroll", "waitlist_enabled"])
            program_map[code] = prog
            label = "auto-enroll" if auto_enroll else "review-and-approve"
            verb = "Created" if prog_created else "Exists"
            self.stdout.write(f"  {verb}: {code} ({label})")

        # ── Admin user ───────────────────────────────────────────────────────
        user, user_created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={
                "email": "admin@sbmusicconservatory.com",
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
                    first, last, _gender = student_pool[student_idx % len(student_pool)]
                    student_idx += 1

                    age_years = rng.randint(4, 18)
                    dob = (now.date() - timedelta(days=age_years * 365 + rng.randint(0, 364)))

                    g_first = rng.choice(GUARDIAN_FIRST)
                    g_email = f"{g_first.lower()}.{last.lower()}@example.com"
                    area = rng.choice(["310", "424", "562"])
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
                            "instrument": f"program:{prog_code}",
                            "lesson_length": rng.choice(["15min", "30min", "45min", "60min", "unsure"]),
                            "experience_level": rng.choice(["none", "beginner", "intermediate", "advanced", "transfer"]),
                            "preferred_days": preferred_days,
                            "preferred_time": rng.choice(["morning", "afternoon", "evening", "flexible"]),
                            "guardian_name": f"{g_first} {last}",
                            "guardian_email": g_email,
                            "guardian_phone": g_phone,
                            "relationship": rng.choice(["mother", "father", "guardian", "self"]),
                            "how_did_you_hear": rng.choice(["google", "referral", "social_media", "drove_by", "current_student", "other"]),
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
            f"\n Done."
            f"\n  Admin:   {base}/schools/{SCHOOL_SLUG}/admin/"
            f"\n  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
            f"\n  Demo:    {demo_base}/demo/sbmc-demo/"
            f"\n  Form:    {base}/schools/{SCHOOL_SLUG}/apply/"
        ))
