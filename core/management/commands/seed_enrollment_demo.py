"""
Seed realistic demo data for the enrollment-request-demo school.

Clears existing submissions and leads, then creates ~35 submissions
spread over the last 90 days + 6 leads in various pipeline stages.

Usage:
    python manage.py seed_enrollment_demo
    python manage.py seed_enrollment_demo --no-wipe   # append only
"""
import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Lead, School, Submission, LEAD_STATUS_NEW

SLUG = "enrollment-request-demo"

# ── Realistic names ────────────────────────────────────────────────────────────

STUDENTS = [
    ("Mia", "Chen"),
    ("Lucas", "Patel"),
    ("Sophia", "Nguyen"),
    ("Ethan", "Rivera"),
    ("Ava", "Okafor"),
    ("Noah", "Kim"),
    ("Isabella", "Morales"),
    ("Liam", "Hassan"),
    ("Charlotte", "Singh"),
    ("Oliver", "Fernandez"),
    ("Amelia", "Tanaka"),
    ("Elijah", "Brooks"),
    ("Harper", "Diaz"),
    ("James", "Andersen"),
    ("Evelyn", "Carter"),
    ("Benjamin", "Wu"),
    ("Abigail", "Mitchell"),
    ("Mason", "Torres"),
    ("Emily", "Clarke"),
    ("Logan", "Park"),
    ("Elizabeth", "Johansson"),
    ("Aiden", "Reyes"),
    ("Chloe", "Nguyen"),
    ("Jacob", "Osei"),
    ("Ella", "Yamamoto"),
    ("Michael", "Hoffman"),
    ("Scarlett", "Bakshi"),
    ("Daniel", "O'Brien"),
    ("Grace", "Pham"),
    ("Matthew", "Lindqvist"),
    ("Zoe", "Adesanya"),
    ("Henry", "Gupta"),
    ("Lily", "Nakamura"),
    ("Sebastian", "Ferreira"),
    ("Nora", "Petrov"),
]

GUARDIANS = [
    ("Jennifer", "Chen"),
    ("Raj", "Patel"),
    ("Linh", "Nguyen"),
    ("Carlos", "Rivera"),
    ("Adaeze", "Okafor"),
    ("Soo-Jin", "Kim"),
    ("Rosa", "Morales"),
    ("Fatima", "Hassan"),
    ("Priya", "Singh"),
    ("Ana", "Fernandez"),
    ("Keiko", "Tanaka"),
    ("Denise", "Brooks"),
    ("Maria", "Diaz"),
    ("Lars", "Andersen"),
    ("Susan", "Carter"),
    ("Wei", "Wu"),
    ("Patricia", "Mitchell"),
    ("Elena", "Torres"),
    ("Fiona", "Clarke"),
    ("Mi-Rae", "Park"),
]

NOTES = [
    "Child has previous experience at another studio.",
    "Family relocating from out of state — flexible start date.",
    "Twin siblings — may enroll both if first trial goes well.",
    "Referred by the Okafor family.",
    "Parent asked about sibling discount.",
    "Student has a performance background — interested in competitive track.",
    "Prefers evening sessions due to school schedule.",
    "Requested Saturday morning specifically.",
    "Asked about payment plan options during intake call.",
    "Parent mentioned student is shy — needs gentle onboarding.",
    "",
    "",
    "",
    "",
    "",
]

LEAD_NAMES = [
    ("Sarah", "Thompson", "website", "sarah.thompson@gmail.com", "(310) 555-0182"),
    ("Marcus", "Edwards", "referral", "medwards@outlook.com", "(424) 555-0247"),
    ("Priya", "Kapoor", "social", "priya.kapoor@icloud.com", "(323) 555-0391"),
    ("Tom", "Vasquez", "website", "tvasquez@gmail.com", "(213) 555-0456"),
    ("Anita", "Johal", "event", "anita.johal@yahoo.com", "(818) 555-0518"),
    ("Derek", "Obi", "referral", "derek.obi@gmail.com", "(626) 555-0634"),
]


class Command(BaseCommand):
    help = "Seed realistic demo data for enrollment-request-demo"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-wipe",
            action="store_true",
            help="Append submissions without deleting existing data",
        )

    def handle(self, *args, **options):
        try:
            school = School.objects.get(slug=SLUG)
        except School.DoesNotExist:
            self.stderr.write(f"School '{SLUG}' not found.")
            return

        if not options["no_wipe"]:
            deleted_subs = Submission.objects.filter(school=school).delete()[0]
            deleted_leads = Lead.objects.filter(school=school).delete()[0]
            self.stdout.write(f"Wiped {deleted_subs} submissions, {deleted_leads} leads.")

        now = timezone.now()
        rng = random.Random(42)

        # ── Submissions ────────────────────────────────────────────────────────

        # (days_ago_min, days_ago_max, status, count)
        # Spread across two 30-day windows so comparison tiles show a real delta.
        batches = [
            # Previous period (31–60 days ago) — fills the comparison column
            (50, 60, "Enrolled",         3),
            (40, 55, "Closed",           2),
            (35, 50, "Archived",         1),
            (31, 48, "Contacted",        4),
            (31, 45, "In Review",        4),
            # Current period (0–30 days) — what the default view shows
            (24, 30, "Enrolled",         3),
            (18, 28, "Enrolled",         3),
            (14, 25, "In Review",        5),
            (10, 20, "Needs Follow Up",  3),
            ( 5, 15, "Contacted",        4),
            ( 3, 10, "In Review",        3),
            ( 1,  7, "New",              5),
            ( 0,  4, "New",              4),
        ]

        student_pool = list(STUDENTS)
        rng.shuffle(student_pool)
        student_idx = 0

        enrolled_subs = []

        for days_min, days_max, status, count in batches:
            for _ in range(count):
                if student_idx >= len(student_pool):
                    student_idx = 0
                first, last = student_pool[student_idx]
                student_idx += 1

                age = rng.randint(6, 17)
                dob = (now.date() - timedelta(days=age * 365 + rng.randint(0, 364)))

                program = rng.choices(
                    ["beginner", "intermediate", "advanced", "unsure"],
                    weights=[35, 30, 25, 10],
                )[0]

                enrollment_type = rng.choices(
                    ["enroll_now", "trial", "returning"],
                    weights=[55, 30, 15],
                )[0]

                start = rng.choices(
                    ["asap", "two_weeks", "one_month", "unsure"],
                    weights=[30, 35, 25, 10],
                )[0]

                days_choices = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                preferred_days = rng.sample(days_choices, k=rng.randint(0, 3))

                time_choice = rng.choices(
                    ["morning", "afternoon", "evening", "flexible", ""],
                    weights=[25, 30, 20, 15, 10],
                )[0]

                has_guardian = age < 16
                g_first = g_last = g_email = g_phone = ""
                if has_guardian and student_idx - 1 < len(GUARDIANS):
                    gf, gl = GUARDIANS[(student_idx - 1) % len(GUARDIANS)]
                    g_first, g_last = gf, gl
                    g_email = f"{gf.lower().replace(' ', '.')}.{gl.lower()}@example.com"
                    area = rng.choice(["310", "424", "323", "213", "818", "626"])
                    g_phone = f"({area}) 555-{rng.randint(1000, 9999)}"

                contact_email = (
                    g_email if g_email
                    else f"{first.lower()}.{last.lower()}@example.com"
                )
                area = rng.choice(["310", "424", "323", "213", "818", "626"])
                contact_phone = (
                    f"({area}) 555-{rng.randint(1000, 9999)}"
                    if rng.random() > 0.3 else ""
                )

                note = rng.choice(NOTES)

                data = {
                    "student_first_name": first,
                    "student_last_name": last,
                    "date_of_birth": dob.isoformat(),
                    "age": float(age),
                    "interested_in": program,
                    "enrollment_type": enrollment_type,
                    "desired_start_date": start,
                    "preferred_days": preferred_days,
                    "preferred_time": time_choice,
                    "guardian_name": f"{g_first} {g_last}".strip() if g_first else "",
                    "guardian_email": g_email,
                    "guardian_phone": g_phone,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                    "notes": note,
                    "id_document": None,
                }

                days_ago = rng.randint(days_min, days_max)
                created = now - timedelta(days=days_ago, hours=rng.randint(0, 10))

                sub = Submission.objects.create(school=school, status=status, data=data)
                Submission.objects.filter(pk=sub.pk).update(created_at=created)
                sub.refresh_from_db()

                if status == "Enrolled":
                    enrolled_subs.append(sub)

        subs_created = Submission.objects.filter(school=school).count()
        self.stdout.write(f"Created {subs_created} submissions.")

        # ── Leads ──────────────────────────────────────────────────────────────

        # 2 converted (→ enrolled submissions), 2 in pipeline, 1 lost, 1 new
        lead_statuses = ["enrolled", "enrolled", "contacted", "trial_scheduled", "lost", "new"]
        rng.shuffle(lead_statuses)

        for i, (first, last, source, email, phone) in enumerate(LEAD_NAMES):
            status = lead_statuses[i]
            converted_sub = None
            converted_at = None

            if status == "enrolled" and enrolled_subs:
                converted_sub = enrolled_subs.pop(0)
                converted_at = now - timedelta(days=rng.randint(5, 30))

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

            # back-date creation for the older ones
            days_ago = rng.randint(10, 60)
            Lead.objects.filter(pk=lead.pk).update(
                created_at=now - timedelta(days=days_ago)
            )

        leads_created = Lead.objects.filter(school=school).count()
        self.stdout.write(f"Created {leads_created} leads.")
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Visit: http://127.0.0.1:8000/schools/{SLUG}/admin/"
        ))
