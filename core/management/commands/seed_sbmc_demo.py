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

from core.models import Lead, School, SchoolAdminMembership, SchoolCustomToken, SchoolEmailTemplate, SchoolProgram, Submission

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


_EMAIL_TEMPLATES = [
    {
        "name": "Trial Lesson Confirmation",
        "subject": "Looking forward to your trial lesson",
        "body": (
            "<p>Hi {{first_name}},</p>"
            "<p>We're so glad you've scheduled a trial lesson with South Bay Music Conservatory "
            "— we look forward to welcoming you and [Student Name] to the studio.</p>"
            "<p>Before your visit, here's a quick overview of what to expect.</p>"
            "<p>At SBMC, we focus on long-term musical growth within a supportive, thoughtful "
            "learning environment. Trial lessons are designed to help us get to know each student, "
            "assess musical readiness, and determine the best next steps.</p>"
            "<p><b>During the trial lesson, the teacher will:</b></p>"
            "<ul>"
            "<li>Work directly with your child</li>"
            "<li>Assess level and learning style</li>"
            "<li>Answer questions</li>"
            "<li>Offer recommendations for placement and next steps</li>"
            "</ul>"
            "<p>[For string students: an appropriately sized instrument will be available for use "
            "during the trial, so there's nothing you need to bring.]</p>"
            "<p><b>Location</b><br>"
            "South Bay Music Conservatory<br>"
            "1407 Crenshaw Blvd., Suite 100<br>"
            "Torrance, CA 90501</p>"
            "<p>Free parking is available in the large lot behind the building. We're located in "
            "the smaller building directly across from Clare Skin Care.</p>"
            "<p><b>Tuition &amp; Fees</b></p>"
            "<ul>"
            "<li>Trial lesson: $30</li>"
            "<li>Weekly private lessons begin at $225/month for 30-minute lessons, which is the "
            "standard starting length for beginners</li>"
            "<li>Longer lesson times are available and may be recommended based on age, level, and goals</li>"
            "<li>One-time Conservatory Enrollment &amp; Placement Fee (at enrollment): $125</li>"
            "</ul>"
            "<p>As a welcome to new families, we're currently offering $100 off your first month "
            "of tuition following enrollment.</p>"
            "<p>You'll receive a reminder from our scheduling system prior to your lesson with "
            "timing and location details. If any questions come up before your visit, feel free to "
            "reply to this email — our team is happy to help.</p>"
            "<p>Warmly,<br>Emily Moore</p>"
        ),
    },
    {
        "name": "Trial Lesson Follow-Up",
        "subject": "Trial Lesson Follow-Up & Placement Recommendation for {{full_name}}",
        "body": (
            "<p>Hi {{first_name}}!</p>"
            "<p>Thank you again for coming in for your trial lesson at South Bay Music "
            "Conservatory. It was a pleasure working with {{full_name}}, and we appreciated "
            "the opportunity to learn more about [his/her] musical background and goals.</p>"
            "<p><b>Trial Lesson Assessment</b></p>"
            "<p>During the trial lesson, we focused on musical readiness, learning style, and "
            "foundational technique.</p>"
            "<p>[1–2 sentence personalized assessment: engagement, focus, enthusiasm, strengths, "
            "potential fit.]</p>"
            "<p><b>Placement Recommendation</b></p>"
            "<p>Based on today's assessment, we recommend:</p>"
            "<ul>"
            "<li>[Lesson Length] weekly private lessons</li>"
            "<li>A consistent weekly lesson time to support steady progress and continuity</li>"
            "</ul>"
            "<p><b>Tuition &amp; Enrollment Fees</b></p>"
            "<p>Our recommended program includes the following:</p>"
            "<ul>"
            "<li>Monthly Tuition: $[Monthly Tuition Amount] / month</li>"
            "<li>One-Time Conservatory Enrollment &amp; Placement Fee: $125</li>"
            "<li>[Optional: Instrument Rental for $35/month]</li>"
            "</ul>"
            "<p>The Conservatory Enrollment &amp; Placement Fee covers initial student assessment "
            "and level placement, teacher matching and schedule coordination, and enrollment "
            "processing and studio onboarding. This fee is charged once per student at initial "
            "enrollment.</p>"
            "<p><b>Spring Enrollment Courtesy</b></p>"
            "<p>To welcome new students enrolling for [Term / Start Period], we're currently "
            "offering $100 off the first month of tuition.</p>"
            "<p><b>Enrollment &amp; Scheduling</b></p>"
            "<p>If you'd like to move forward, you may complete enrollment here.</p>"
            "<p>And submit your scheduling preferences here.</p>"
            "<p>Once enrolled, you'll automatically receive scheduling confirmation, onboarding "
            "details, and your SBMC Welcome Packet.</p>"
            "<p>If you'd prefer to talk through placement or scheduling options before enrolling, "
            "feel free to reply to this email — I'm always happy to help.</p>"
            "<p><b>Studio Policies</b></p>"
            "<p>As part of enrollment, families review and sign our studio policies covering "
            "scheduling, attendance, billing, and communication. You'll review and sign them "
            "during enrollment, and may preview them in advance here.</p>"
            "<p>We truly enjoyed meeting {{full_name}}, and would be delighted to continue "
            "supporting [his/her] musical growth at SBMC.</p>"
            "<p>Warm regards,<br>Emily</p>"
            "<p>P.S., join us at our annual picnic on July 12th? Come see the studio in action!</p>"
        ),
    },
    {
        "name": "Registration Confirmation",
        "subject": "Welcome to South Bay Music Conservatory!",
        "body": (
            "<p>Hi {{first_name}},</p>"
            "<p>Welcome to South Bay Music Conservatory — we're so happy to have you join our "
            "studio community! Your enrollment is complete, and we're looking forward to working "
            "together as {{full_name}} begins [his/her] musical journey with us.</p>"
            "<p>We are confirming [LENGTH] [INSTRUMENT] lessons with [TEACHER] on [DAY] at [TIME], "
            "beginning [DATE].</p>"
            "<p><b>Billing overview:</b></p>"
            "<ul>"
            "<li>Your card on file will be charged $125 for the one-time Conservatory Assessment "
            "&amp; Placement Fee.</li>"
            "<li>The lesson next week will be billed at the single-lesson rate of $50.</li>"
            "<li>Ongoing monthly tuition of [PRICE] will begin on [DATE] and will be billed on "
            "the 1st of each month moving forward.</li>"
            "<li>The first-month 50% discount will be applied as a courtesy refund after the "
            "second full month of tuition is paid.</li>"
            "</ul>"
            "<p>To help you get oriented, we've put together a Welcome Packet with everything "
            "you need to know as you get started, including:</p>"
            "<ul>"
            "<li>Studio policies and expectations</li>"
            "<li>Communication and scheduling guidelines</li>"
            "<li>What to have ready at home for lessons</li>"
            "<li>An overview of community and performance opportunities at SBMC</li>"
            "</ul>"
            "<p>Access the Welcome Packet here.</p>"
            "<p>For planning purposes, please note that the most up-to-date Semester Calendar "
            "— including recital dates, studio closures, and upcoming community events — lives on "
            "our website and may be updated as needed throughout the term.</p>"
            "<p>View the Semester Calendar here. We recommend bookmarking this page for easy "
            "reference.</p>"
            "<p>Please take a few minutes to review the Welcome Packet before your first lesson. "
            "It will answer many common questions and help ensure a smooth start.</p>"
            "<p>If anything comes up, or if you're unsure about next steps, you're always welcome "
            "to reach out. We're here to support you and are excited to be part of your "
            "musical growth.</p>"
            "<p>Warmly,<br>Emily Moore</p>"
        ),
    },
    {
        "name": "Update Payment Method",
        "subject": "Action Needed: Tuition Payment Update",
        "body": (
            "<p>Hi {{first_name}},</p>"
            "<p>I hope you're doing well!</p>"
            "<p>It looks like your recent tuition payment was unable to be processed. This is "
            "often due to an expired card, a new card number, or a payment method that needs "
            "to be updated.</p>"
            "<p>When you have a moment, please log in to your account and update your payment "
            "information. Once it's updated, the outstanding tuition balance will process "
            "automatically.</p>"
            "<p>If you've already taken care of this, thank you! No further action is needed.</p>"
            "<p>If you have any questions or need assistance, please don't hesitate to reach out. "
            "I'm always happy to help.</p>"
            "<p>Thank you for your prompt attention, and I look forward to seeing you at the "
            "studio!</p>"
            "<p>Thanks,<br>Emily</p>"
        ),
    },
]


_CUSTOM_TOKENS = [
    ("teacher",        "Teacher name"),
    ("day",            "Lesson day"),
    ("time",           "Lesson time"),
    ("date",           "Start date"),
    ("lesson_length",  "Lesson length"),
    ("instrument",     "Instrument"),
    ("price",          "Monthly tuition"),
    ("enrollment_fee", "Enrollment fee"),
]


def _seed_custom_tokens(school, stdout=None):
    created = 0
    for key, label in _CUSTOM_TOKENS:
        _, c = SchoolCustomToken.objects.get_or_create(
            school=school, key=key, defaults={"label": label}
        )
        if c:
            created += 1
    if stdout:
        verb = f"Created {created}" if created else "All already exist"
        stdout.write(f"  {verb}: custom tokens ({len(_CUSTOM_TOKENS)} total).")


def _seed_email_templates(school, stdout=None):
    created_count = 0
    for tmpl in _EMAIL_TEMPLATES:
        _, created = SchoolEmailTemplate.objects.update_or_create(
            school=school,
            name=tmpl["name"],
            defaults={"subject": tmpl["subject"], "body": tmpl["body"]},
        )
        if created:
            created_count += 1
    if stdout:
        verb = f"Created {created_count}" if created_count else "All already exist"
        stdout.write(f"  {verb}: email templates ({len(_EMAIL_TEMPLATES)} total).")


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

        # ── Email templates + custom tokens ─────────────────────────────────
        _seed_custom_tokens(school, stdout=self.stdout)
        _seed_email_templates(school, stdout=self.stdout)

        from django.conf import settings
        demo_base = getattr(settings, "DEMO_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
        self.stdout.write(self.style.SUCCESS(
            f"\n Done."
            f"\n  Admin:   {demo_base}/schools/{SCHOOL_SLUG}/admin/"
            f"\n  Login:   {ADMIN_USERNAME} / {ADMIN_PASSWORD}"
            f"\n  Demo:    {demo_base}/demo/sbmc-demo/"
            f"\n  Form:    {demo_base}/schools/{SCHOOL_SLUG}/apply/"
        ))
