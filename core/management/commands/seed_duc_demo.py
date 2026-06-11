"""
Set up DUC Learning Center demo: school, programs, admin user, and sample submissions.

Usage:
    python manage.py seed_duc_demo
    python manage.py seed_duc_demo --force   # re-seed submissions even if data exists

Idempotent: school/programs/user are get_or_created; submissions skipped if >= 5 exist
unless --force is passed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import School, SchoolAdminMembership, SchoolProgram, Submission

User = get_user_model()

SCHOOL_SLUG = "duc-learning-center"
ADMIN_USERNAME = "duc_admin"
ADMIN_PASSWORD = "DucAdmin@123"

PROGRAMS = [
    ("1-on-1 Tutoring", "tutoring"),
    ("Homeschool Support", "homeschool_support"),
    ("Afterschool Program", "afterschool_program"),
    ("College Help", "college_help"),
    ("Teen Excel Workshops", "teen_workshops"),
    ("Parent-to-Student Tutoring Training", "parent_student_tutoring_training"),
    ("Student Life Coaching", "student_life_coaching"),
    ("Parental Life Coaching", "parental_life_coaching"),
    ("Elementary/Middle School Small Groups", "small_groups"),
]

SUBMISSIONS = [
    {
        "first": "Aaliyah", "last": "Jackson", "email": "ajackson@gmail.com",
        "grade": "high_school", "program": "tutoring", "status": "Enrolled", "days_ago": 14,
    },
    {
        "first": "Marcus", "last": "Williams", "email": "mwilliams@yahoo.com",
        "grade": "middle_school", "program": "afterschool_program", "status": "Enrolled", "days_ago": 12,
    },
    {
        "first": "Sofia", "last": "Reyes", "email": "sreyes@gmail.com",
        "grade": "elementary", "program": "homeschool_support", "status": "Enrolled", "days_ago": 10,
    },
    {
        "first": "Jordan", "last": "Thompson", "email": "jthompson@gmail.com",
        "grade": "high_school", "program": "college_help", "status": "In Review", "days_ago": 7,
    },
    {
        "first": "Destiny", "last": "Brown", "email": "dbrown@gmail.com",
        "grade": "middle_school", "program": "teen_workshops", "status": "In Review", "days_ago": 6,
    },
    {
        "first": "Elijah", "last": "Davis", "email": "edavis@yahoo.com",
        "grade": "high_school", "program": "student_life_coaching", "status": "Contacted", "days_ago": 5,
    },
    {
        "first": "Amara", "last": "Johnson", "email": "amara.j@gmail.com",
        "grade": "middle_school", "program": "small_groups", "status": "New", "days_ago": 3,
    },
    {
        "first": "Noah", "last": "Martinez", "email": "nmartinez@gmail.com",
        "grade": "elementary", "program": "tutoring", "status": "New", "days_ago": 2,
    },
    {
        "first": "Zoe", "last": "Harris", "email": "zharris@gmail.com",
        "grade": "high_school", "program": "tutoring", "status": "Waitlisted", "days_ago": 8,
    },
    {
        "first": "Isaiah", "last": "Clark", "email": "iclark@gmail.com",
        "grade": "college_adult", "program": "parental_life_coaching", "status": "New", "days_ago": 1,
    },
]


class Command(BaseCommand):
    help = "Set up DUC Learning Center demo school, programs, admin user, and sample submissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed submissions even if they already exist.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        # ── School ───────────────────────────────────────────────────────────
        school, created = School.objects.get_or_create(
            slug=SCHOOL_SLUG,
            defaults={
                "display_name": "DUC Learning Center",
                "plan": "starter",
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(f"  Created school: {school.slug}")
        else:
            self.stdout.write(f"  School exists: {school.slug}")

        # Ensure is_active even if school pre-existed as inactive
        if not school.is_active:
            school.is_active = True
            school.save(update_fields=["is_active"])

        # ── Programs ─────────────────────────────────────────────────────────
        program_map = {}
        for name, code in PROGRAMS:
            prog, created = SchoolProgram.objects.get_or_create(
                school=school,
                code=code,
                defaults={"name": name, "is_active": True},
            )
            program_map[code] = prog
            if created:
                self.stdout.write(f"  Created program: {code}")

        # Ensure program_field_key is set on the school
        if school.program_field_key != "interested_in":
            school.program_field_key = "interested_in"
            school.save(update_fields=["program_field_key"])
            self.stdout.write("  Set program_field_key = interested_in")

        # ── Admin user ───────────────────────────────────────────────────────
        user, created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={
                "email": "duc_admin@duclearningcenter.org",
                "is_staff": True,
                "is_superuser": False,
                "is_active": True,
            },
        )
        if created:
            user.set_password(ADMIN_PASSWORD)
            user.save()
            self.stdout.write(f"  Created user: {ADMIN_USERNAME}")
        else:
            # Ensure is_staff even if user pre-existed without it
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
                self.stdout.write(f"  Fixed is_staff for: {ADMIN_USERNAME}")
            else:
                self.stdout.write(f"  User exists: {ADMIN_USERNAME}")

        SchoolAdminMembership.objects.get_or_create(user=user, school=school)

        # ── Submissions ──────────────────────────────────────────────────────
        existing = Submission.objects.filter(school=school).count()
        if existing >= 5 and not opts["force"]:
            self.stdout.write(
                f"  Skipping submissions — {existing} already exist. Use --force to re-seed."
            )
        else:
            now = datetime.now(tz=timezone.utc)
            count = 0
            for s in SUBMISSIONS:
                prog = program_map.get(s["program"])
                sub = Submission.objects.create(
                    school=school,
                    data={
                        "student_first_name": s["first"],
                        "student_last_name": s["last"],
                        "guardian_name": f"{s['first'][0]}. {s['last']} Parent",
                        "guardian_email": s["email"],
                        "guardian_phone": "(951) 555-0100",
                        "grade_level": s["grade"],
                        "interested_in": f"program:{s['program']}",
                        "enrollment_type": "enroll_now",
                        "preferred_location": "desert_hot_springs",
                        "preferred_time": "afternoon",
                        "goals": "Help with keeping up in class and building confidence.",
                    },
                    status=s["status"],
                    program=prog,
                )
                back_dated = now - timedelta(days=s["days_ago"])
                Submission.objects.filter(pk=sub.pk).update(created_at=back_dated)
                count += 1
            self.stdout.write(f"  Created {count} submissions.")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Login at /schools/{SCHOOL_SLUG}/admin/ "
            f"with {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
        ))
