"""
Set up Kid Works Children's Center demo: school, programs, and admin user.

Usage:
    python manage.py seed_kidworks_demo              # school + programs + admin only (no submissions)
    python manage.py seed_kidworks_demo --seed-data  # also seed sample submissions and leads
    python manage.py seed_kidworks_demo --seed-data --force  # re-seed even if data exists

Programs:
  Bungalow (Ages 2), Garden Room (Ages 3), Sun Room (Ages 4–5),
  Mindfulness & Yoga, Growing in the Garden
"""
from __future__ import annotations

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Lead, School, SchoolAdminMembership, SchoolProgram, Submission

User = get_user_model()

SCHOOL_SLUG = "kidworks-childrens-center"
ADMIN_USERNAME = "kidworks_admin"
ADMIN_PASSWORD = "KidworksAdmin@123"

# (name, code, auto_enroll, waitlist_enabled)
PROGRAMS = [
    ("Bungalow Full Day (Ages 2)", "bungalow_full_day", True, True),
    ("Bungalow Half Day (Ages 2)", "bungalow_half_day", True, True),
    ("Garden Room Full Day (Ages 3)", "garden_room_full_day", True, True),
    ("Garden Room Half Day (Ages 3)", "garden_room_half_day", True, True),
    ("Sun Room Full Day (Ages 4–5)", "sun_room_full_day", True, True),
    ("Sun Room Half Day (Ages 4–5)", "sun_room_half_day", True, True),
]

# Long Beach–area family names
STUDENTS = [
    ("Sofia",    "Reyes",      "female"),
    ("Emma",     "Nakamura",   "female"),
    ("Olivia",   "Johnson",    "female"),
    ("Mia",      "Hernandez",  "female"),
    ("Ava",      "Kim",        "female"),
    ("Luna",     "Moreau",     "female"),
    ("Chloe",    "Williams",   "female"),
    ("Harper",   "Ramirez",    "female"),
    ("Lily",     "Tanaka",     "female"),
    ("Aria",     "Patel",      "female"),
    ("Noah",     "Martinez",   "male"),
    ("Liam",     "Thompson",   "male"),
    ("Ethan",    "Park",       "male"),
    ("Lucas",    "Robinson",   "male"),
    ("Mason",    "Torres",     "male"),
    ("Oliver",   "Chen",       "male"),
    ("Mateo",    "Gomez",      "male"),
    ("Elijah",   "Nguyen",     "male"),
    ("Aiden",    "Brown",      "male"),
    ("Sebastian","Lopez",      "male"),
    ("Isabella", "Davis",      "female"),
    ("Camila",   "Sanchez",    "female"),
    ("Nora",     "Lee",        "female"),
    ("Elena",    "Flores",     "female"),
    ("Laila",    "Wilson",     "female"),
]

GUARDIAN_FIRST = [
    "Maria", "Jennifer", "Patricia", "Linda", "Barbara",
    "Michael", "James", "Robert", "John", "David",
    "Sarah", "Karen", "Jessica", "Lisa", "Nancy",
    "Daniel", "Kevin", "Brian", "Edward", "Thomas",
]

NOTES_EXAMPLES = [
    "Child has a nut allergy — please confirm kitchen protocols.",
    "We toured the center last month and loved the garden classroom.",
    "We're moving to Long Beach in the spring — hoping to start in September.",
    "Our older child attended Kid Works — excited to bring our second.",
    "",
    "",
    "",
]

# (days_ago_min, days_ago_max, status, program_code, count)
BATCHES = [
    # Previous period (31–60 days) — comparison column in reports
    (50, 60, "Enrolled",         "sun_room_full_day",      2),
    (45, 58, "Enrolled",         "bungalow_full_day",      2),
    (40, 55, "Enrolled",         "garden_room_full_day",   2),
    (35, 52, "Enrolled",         "bungalow_half_day",      1),
    (33, 50, "Enrolled",         "garden_room_half_day",   1),
    (31, 48, "Contacted",        "sun_room_half_day",      2),
    (31, 45, "Waitlisted",       "bungalow_full_day",      1),
    (31, 44, "New",              "sun_room_half_day",      2),
    # Current period (0–30 days)
    (24, 30, "Enrolled",         "sun_room_full_day",      2),
    (20, 28, "Enrolled",         "garden_room_full_day",   2),
    (18, 26, "Enrolled",         "bungalow_full_day",      2),
    (17, 25, "Enrolled",         "bungalow_half_day",      1),
    (15, 25, "Tour Scheduled",   "sun_room_full_day",      2),
    (12, 22, "Tour Scheduled",   "garden_room_half_day",   1),
    (10, 20, "In Review",        "garden_room_full_day",   2),
    (10, 18, "In Review",        "sun_room_half_day",      1),
    ( 7, 15, "Contacted",        "bungalow_full_day",      2),
    ( 5, 12, "Needs Follow Up",  "sun_room_full_day",      2),
    ( 4, 10, "Waitlisted",       "bungalow_full_day",      1),
    ( 3,  8, "New",              "garden_room_half_day",   3),
    ( 2,  7, "New",              "bungalow_half_day",      2),
    ( 1,  5, "New",              "bungalow_full_day",      3),
    ( 0,  3, "New",              "sun_room_half_day",      2),
]

LEADS = [
    ("Jessica Park",    "referral",  "jessica.park@gmail.com",     "(562) 555-0181"),
    ("Carlos Mendez",   "google",    "cmendez@icloud.com",          "(562) 555-0247"),
    ("Rachel Thompson", "drove_by",  "rthompson@yahoo.com",         "(562) 555-0312"),
    ("Kevin Nakamura",  "social",    "k.nakamura@gmail.com",        "(310) 555-0429"),
    ("Priya Patel",     "referral",  "priya.patel@outlook.com",     "(562) 555-0538"),
    ("Marcus Williams", "website",   "mwilliams@gmail.com",         "(562) 555-0664"),
]


class Command(BaseCommand):
    help = "Set up Kid Works Children's Center demo school, programs, and admin user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed-data",
            action="store_true",
            help="Also seed sample submissions and leads.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed submissions/leads even if they already exist (requires --seed-data).",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        seed_data = opts["seed_data"]
        force = opts["force"]
        rng = random.Random(77)

        from django.conf import settings
        demo_base = getattr(settings, "DEMO_BASE_URL", "http://127.0.0.1:8001").rstrip("/")

        # ── School ────────────────────────────────────────────────────────────
        school, created = School.objects.get_or_create(
            slug=SCHOOL_SLUG,
            defaults={
                "display_name": "Kid Works Children's Center",
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

        school.trial_started_at = timezone.now() - timedelta(days=3)
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

        # ── Programs ──────────────────────────────────────────────────────────
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
            if not prog.is_active:
                prog.is_active = True
                prog.save(update_fields=["is_active"])
            program_map[code] = prog
            self.stdout.write(f"  {'Created' if prog_created else 'Exists'}: program {code}")

        # ── Admin user ────────────────────────────────────────────────────────
        user, user_created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={"is_staff": True, "is_superuser": False},
        )
        if user_created:
            user.set_password(ADMIN_PASSWORD)
            user.save()
        SchoolAdminMembership.objects.get_or_create(user=user, school=school)
        self.stdout.write(f"  {'Created' if user_created else 'Exists'}: user {ADMIN_USERNAME}")

        if not seed_data:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Setup complete (no sample data seeded)."))
            self.stdout.write(f"  Form:    {demo_base}/schools/{SCHOOL_SLUG}/apply/")
            self.stdout.write(f"  Admin:   {demo_base}/schools/{SCHOOL_SLUG}/admin/")
            self.stdout.write(f"  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
            self.stdout.write(f"  Demo:    {demo_base}/demo/kidworks-demo/")
            self.stdout.write("")
            self.stdout.write("  Run with --seed-data to add sample submissions and leads.")
            return

        # ── Submissions ───────────────────────────────────────────────────────
        existing_count = Submission.objects.filter(school=school).count()
        if existing_count >= 5 and not force:
            self.stdout.write(f"  Skipping submissions ({existing_count} exist; use --force to re-seed).")
        else:
            if force and existing_count:
                Submission.objects.filter(school=school).delete()
                self.stdout.write(f"  Deleted {existing_count} existing submissions.")
            students = list(STUDENTS)
            rng.shuffle(students)
            idx = 0
            created_count = 0
            now = timezone.now()

            for days_min, days_max, status, prog_code, count in BATCHES:
                prog = program_map.get(prog_code)
                if not prog:
                    continue
                for _ in range(count):
                    first, last, gender = students[idx % len(students)]
                    idx += 1
                    guardian_first = rng.choice(GUARDIAN_FIRST)
                    days_ago = rng.randint(days_min, days_max)
                    submitted_at = now - timedelta(days=days_ago, hours=rng.randint(0, 8))
                    dob_days = rng.randint(730, 2190)

                    sub = Submission.objects.create(
                        school=school,
                        status=status,
                        data={
                            "child_first_name": first,
                            "child_last_name": last,
                            "date_of_birth": (now - timedelta(days=dob_days)).strftime("%Y-%m-%d"),
                            "gender": gender,
                            "program_interest": prog_code,
                            "guardian_name": f"{guardian_first} {last}",
                            "guardian_email": f"{guardian_first.lower()}.{last.lower()}@gmail.com",
                            "guardian_phone": f"(562) 555-{rng.randint(1000, 9999)}",
                            "relationship": rng.choice(["mother", "father", "guardian"]),
                            "potty_trained": rng.choice(["yes", "in_progress", "no"]),
                            "how_did_you_hear": rng.choice(["referral", "google", "drove_by", "social_media"]),
                            "notes": rng.choice(NOTES_EXAMPLES),
                        },
                        program=prog,
                        internal_notes=rng.choice(NOTES_EXAMPLES) if rng.random() < 0.3 else "",
                    )
                    update_fields = {"created_at": submitted_at}
                    if status == "Needs Follow Up":
                        update_fields["next_follow_up_at"] = now - timedelta(days=rng.randint(1, 5))
                    Submission.objects.filter(pk=sub.pk).update(**update_fields)

                    created_count += 1

            self.stdout.write(f"  Created {created_count} submissions.")

        # ── Leads ─────────────────────────────────────────────────────────────
        existing_leads = Lead.objects.filter(school=school).count()
        if existing_leads >= 3 and not force:
            self.stdout.write(f"  Skipping leads ({existing_leads} exist; use --force to re-seed).")
        else:
            if force and existing_leads:
                Lead.objects.filter(school=school).delete()
                self.stdout.write(f"  Deleted {existing_leads} existing leads.")
            lead_statuses = ["enrolled", "contacted", "trial_scheduled", "new", "new", "contacted"]
            for i, (name, source, email, phone) in enumerate(LEADS):
                status = lead_statuses[i % len(lead_statuses)]
                lead = Lead.objects.create(
                    school=school,
                    name=name,
                    email=email,
                    phone=phone,
                    source=source,
                    status=status,
                    data={"program_interest": rng.choice(list(program_map.keys()))},
                )
                if status == "enrolled" and i == 0:
                    # Link one enrolled lead to a converted submission
                    converted = Submission.objects.filter(school=school, status="Enrolled").first()
                    if converted:
                        Lead.objects.filter(pk=lead.pk).update(
                            converted_submission=converted,
                            converted_at=timezone.now() - timedelta(days=rng.randint(3, 10)),
                        )
                if status == "contacted":
                    Lead.objects.filter(pk=lead.pk).update(
                        next_follow_up_at=timezone.now() - timedelta(days=rng.randint(1, 4))
                    )
            self.stdout.write(f"  Created {len(LEADS)} leads.")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seeding complete."))
        self.stdout.write(f"  Demo:    {demo_base}/demo/kidworks-demo/")
        self.stdout.write(f"  Admin:   {demo_base}/schools/{SCHOOL_SLUG}/admin/")
        self.stdout.write(f"  Form:    {demo_base}/schools/{SCHOOL_SLUG}/apply/")
        self.stdout.write(f"  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
