# core/management/commands/seed_demo_data.py
"""
Seed demo data for a fresh database.

==========================
Render DB creation (scratch)
==========================

1) Render Dashboard → New → PostgreSQL
   - Choose region closest to your web service
   - Create database

2) After it provisions:
   - Render Dashboard → your Postgres → Connect
   - Copy "Internal Database URL" (best for Render web service)
   - (Optional) Copy "External Database URL" for local psql access

3) Render Dashboard → your Web Service → Environment
   - Add DATABASE_URL = <Internal Database URL>
   - Ensure other required env vars exist (SECRET_KEY, DEBUG=False, etc.)

4) Deploy (or redeploy) the web service to pick up DATABASE_URL.

5) Run migrations:
   - Render Dashboard → Web Service → Shell
   - python manage.py migrate

6) Seed demo data:
   - python manage.py seed_demo_data --school-slug enrollment-request-demo

Notes:
- You generally do NOT create the DB manually; Render provisions it.
- Django migrations create tables.
- This seed command creates users/school/submissions safely (idempotent-ish).

"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from core.models import School, Submission, SchoolAdminMembership


class Command(BaseCommand):
    help = "Seeds demo data (superuser, a school admin, membership, and demo submissions)."

    def add_arguments(self, parser):
        # Superuser
        parser.add_argument("--superuser-username", default="admin")
        parser.add_argument("--superuser-email", default="admin@example.com")
        parser.add_argument("--superuser-password", default="admin12345")
        parser.add_argument("--skip-superuser", action="store_true", help="Skip creating the superuser")

        # School admin
        parser.add_argument("--school-admin-username", default="schooladmin")
        parser.add_argument("--school-admin-email", default="schooladmin@example.com")
        parser.add_argument("--school-admin-password", default="schooladmin12345")

        # School + submissions
        parser.add_argument("--school-slug", default="enrollment-request-demo")
        parser.add_argument("--school-name", default="Enrollment Request Demo")
        parser.add_argument("--school-plan", default="starter", help="Plan tier for the school (trial, starter, pro, growth)")
        parser.add_argument("--submissions", type=int, default=15)

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()

        # -------------------
        # Create superuser
        # -------------------
        if not opts["skip_superuser"]:
            su, su_created = User.objects.get_or_create(
                username=opts["superuser_username"],
                defaults={
                    "email": opts["superuser_email"],
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if su_created:
                su.set_password(opts["superuser_password"])
                su.save()
                self.stdout.write(self.style.SUCCESS(f"Created superuser: {su.username}"))
            else:
                self.stdout.write(f"Superuser exists: {su.username}")
        else:
            self.stdout.write("Skipping superuser creation (per --skip-superuser)")

        # -------------------
        # Create school
        # -------------------
        school, created = School.objects.get_or_create(
            slug=opts["school_slug"],
            defaults={
                "display_name": opts["school_name"],
                "website_url": "",
                "source_url": "",
                "plan": opts["school_plan"],
            },
        )
        if not created and school.plan != opts["school_plan"]:
            school.plan = opts["school_plan"]
            school.save(update_fields=["plan"])
        self.stdout.write(self.style.SUCCESS(f"School ready: {school.slug} (plan={school.plan})"))

        # -------------------
        # Create school admin user (non-superuser)
        # -------------------
        sa, sa_created = User.objects.get_or_create(
            username=opts["school_admin_username"],
            defaults={
                "email": opts["school_admin_email"],
                "is_staff": True,
                "is_superuser": False,
            },
        )
        if sa_created:
            sa.set_password(opts["school_admin_password"])
            sa.save()
            self.stdout.write(self.style.SUCCESS(f"Created school admin: {sa.username}"))
        else:
            # Ensure is_staff is true (in case user existed)
            if not sa.is_staff:
                sa.is_staff = True
                sa.save(update_fields=["is_staff"])
            self.stdout.write(f"School admin exists: {sa.username}")

        # -------------------
        # Create membership
        # -------------------
        SchoolAdminMembership.objects.get_or_create(
            user=sa,
            defaults={"school": school},
        )
        # If membership exists but points to a different school, update it.
        # (depends on your model constraints; safe for 1:1 membership patterns)
        membership = SchoolAdminMembership.objects.filter(user=sa).first()
        if membership and membership.school_id != school.id:
            membership.school = school
            membership.save(update_fields=["school"])
        self.stdout.write(self.style.SUCCESS("SchoolAdminMembership ready"))

        # -------------------
        # Create demo submissions
        # - Make sure at least one has fields that display well
        # -------------------
        existing = Submission.objects.filter(school=school).count()
        target = opts["submissions"]
        to_make = max(0, target - existing)

        # One “nice” submission that should show well in admin (if your display funcs use these keys)
        if existing == 0:
            Submission.objects.create(
                school=school,
                form_key="default",
                data={
                    "student_first_name": "Demo",
                    "student_last_name": "Student",
                    "date_of_birth": "2016-12-03",
                    "age": 9,
                    "program_interest": "beginner",
                    "contact_email": "demo.student@example.com",
                },
            )
            to_make = max(0, to_make - 1)

        for i in range(to_make):
            Submission.objects.create(
                school=school,
                form_key="default",
                data={
                    "student_first_name": f"Test{i+1}",
                    "student_last_name": "Student",
                    "program_interest": "beginner",
                    "contact_email": f"test{i+1}@example.com",
                },
            )

        total = Submission.objects.filter(school=school).count()
        self.stdout.write(self.style.SUCCESS(f"Submissions ready (total={total})"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seed complete ✅"))
        self.stdout.write(f"Superuser login: {opts['superuser_username']} / {opts['superuser_password']}")
        self.stdout.write(f"School admin login: {opts['school_admin_username']} / {opts['school_admin_password']}")
        self.stdout.write(f"School slug: {opts['school_slug']}")
