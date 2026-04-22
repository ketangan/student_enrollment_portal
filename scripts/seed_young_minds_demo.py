#!/usr/bin/env python3
"""
Seed realistic demo submissions for Young Minds Learning Academy.

Usage:
  cd /path/to/student_enrollment_portal
  python scripts/seed_young_minds_demo.py

Optional:
  python scripts/seed_young_minds_demo.py --count 18 --replace
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.utils import timezone
from core.models import School, Submission

SCHOOL_SLUG = "young-minds-la"
DEFAULT_COUNT = 18

FIRST_NAMES = [
    "Ava", "Liam", "Maya", "Noah", "Ella", "Leo", "Sofia", "Milo", "Isla",
    "Aria", "Ethan", "Zoe", "Nora", "Ezra", "Luna", "Owen", "Ivy", "Mia",
    "Theo", "Ruby", "Kai", "Eliana", "Mason", "Clara", "Jude", "Ari",
]
LAST_NAMES = [
    "Martinez", "Kim", "Patel", "Johnson", "Garcia", "Nguyen", "Cohen",
    "Hernandez", "Lopez", "Singh", "Bennett", "Flores", "Rivera", "Shah",
    "Wilson", "Park", "Torres", "Reed", "Choi", "Morgan", "Ramos", "Diaz",
]
CITIES = ["Los Angeles", "Venice", "Santa Monica", "Mar Vista", "Westchester", "Culver City"]
ZIPS = ["90066", "90034", "90064", "90045", "90291", "90405"]

PROGRAM_WEIGHTS = [
    ("3yr_program", 0.35),
    ("4yr_program", 0.30),
    ("2yr_program", 0.15),
    ("tk_kinder", 0.10),
    ("parent_and_me", 0.10),
]
CAMPUS_WEIGHTS = [
    ("westchester", 0.50),
    ("venice", 0.30),
    ("westla", 0.20),
]
STATUS_WEIGHTS = [
    ("New", 0.40),
    ("Tour Scheduled", 0.25),
    ("Tour Completed", 0.15),
    ("Enrolled", 0.10),
    ("Waitlisted", 0.05),
    ("Declined", 0.05),
]

PREFERRED_TIME_BY_PROGRAM = {
    "2yr_program": ["morning", "morning_lunch_bunch", "extended_day"],
    "3yr_program": ["morning", "morning_lunch_bunch", "extended_day", "late_owls"],
    "4yr_program": ["morning", "extended_day", "late_owls"],
    "tk_kinder": ["extended_day", "late_owls"],
    "parent_and_me": ["morning", "flexible"],
}
DESIRED_START_OPTIONS = ["fall_2025", "spring_2026", "fall_2026", "asap", "unsure"]
HOW_HEARD_OPTIONS = [
    ("google", 0.30),
    ("friend_family", 0.25),
    ("drove_by", 0.15),
    ("instagram_facebook", 0.15),
    ("current_family", 0.10),
    ("yelp", 0.05),
]

ALLERGIES = [
    ("None", 0.65),
    ("Peanut allergy", 0.10),
    ("Dairy sensitivity", 0.08),
    ("Egg allergy", 0.05),
    ("Seasonal pollen allergies", 0.07),
    ("Sesame allergy", 0.05),
]
MEDICAL = [
    ("None", 0.72),
    ("Mild asthma; inhaler available if needed.", 0.10),
    ("Speech therapy support outside school.", 0.06),
    ("Occasional eczema flare-ups.", 0.07),
    ("Epi-pen required for known allergy.", 0.05),
]
NOTES = [
    "",
    "Interested in scheduling a tour in the next two weeks.",
    "Older sibling previously attended and had a wonderful experience.",
    "Child can be shy at drop-off but warms up quickly.",
    "Would love guidance on best-fit program and campus.",
    "Family is relocating to the area this summer.",
    "Looking for extended-day coverage due to work schedules.",
]

PHONE_PREFIXES = ["310", "323", "424", "213"]


def weighted_choice(weighted_items):
    items = [x for x, _ in weighted_items]
    weights = [w for _, w in weighted_items]
    return random.choices(items, weights=weights, k=1)[0]


def normalize_age_for_program(program):
    if program == "2yr_program":
        return 2
    if program == "3yr_program":
        return 3
    if program == "4yr_program":
        return 4
    if program == "tk_kinder":
        return random.choice([5, 6])
    if program == "parent_and_me":
        return random.choice([1, 2])
    return 3


def dob_for_age(age):
    today = timezone.localdate()
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    year = today.year - age
    if (month, day) > (today.month, today.day):
        year -= 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def make_phone():
    return f"{random.choice(PHONE_PREFIXES)}-555-{random.randint(1000, 9999)}"


def unique_email(first, last, idx):
    return f"{first.lower()}.{last.lower()}.{idx}@example.com"


def maybe_second_guardian():
    if random.random() < 0.45:
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        return f"{first} {last}", f"{first.lower()}.{last.lower()}@example.com", make_phone()
    return "", "", ""


def build_submission(idx):
    child_first = random.choice(FIRST_NAMES)
    child_last = random.choice(LAST_NAMES)
    guardian_last = child_last if random.random() < 0.7 else random.choice(LAST_NAMES)
    guardian_first = random.choice(
        ["Emma", "Olivia", "Sophia", "Michael", "Daniel", "Priya", "Sarah", "David", "Alicia", "Kevin", "Rachel", "Anita"]
    )
    guardian_name = f"{guardian_first} {guardian_last}"
    guardian_relationship = random.choices(
        ["mother", "father", "legal_guardian", "grandparent", "other"],
        weights=[0.42, 0.33, 0.12, 0.08, 0.05],
        k=1,
    )[0]

    program = weighted_choice(PROGRAM_WEIGHTS)
    age = normalize_age_for_program(program)
    campus = weighted_choice(CAMPUS_WEIGHTS)
    status = weighted_choice(STATUS_WEIGHTS)
    created_at = timezone.now() - timedelta(
        days=random.randint(1, 45),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )

    guardian_2_name, guardian_email, guardian_phone = maybe_second_guardian()

    data = {
        "student_first_name": child_first,
        "student_last_name": child_last,
        "date_of_birth": dob_for_age(age),
        "age": age,
        "gender": random.choice(["female", "male", "prefer_not_to_say"]),
        "previous_school": random.choice(["", "Little Sprouts Preschool", "Bright Beginnings", "At-home care", "Neighborhood childcare"]),
        "interested_in": program,
        "campus_preference": campus,
        "enrollment_type": random.choices(
            ["new_student", "returning_student", "sibling"], weights=[0.72, 0.12, 0.16], k=1
        )[0],
        "desired_start_date": random.choices(DESIRED_START_OPTIONS, weights=[0.35, 0.10, 0.25, 0.20, 0.10], k=1)[0],
        "preferred_time": random.choice(PREFERRED_TIME_BY_PROGRAM[program]),
        "guardian_name": guardian_name,
        "contact_email": unique_email(guardian_first, guardian_last, idx),
        "contact_phone": make_phone(),
        "guardian_relationship": guardian_relationship,
        "address": f"{random.randint(100, 9999)} {random.choice(['Maple', 'Oak', 'Lincoln', 'Grand', 'Pacific', 'Rose'])} {random.choice(['Ave', 'St', 'Blvd'])}",
        "city": random.choice(CITIES),
        "zip": random.choice(ZIPS),
        "guardian_2_name": guardian_2_name,
        "guardian_email": guardian_email,
        "guardian_phone": guardian_phone,
        "emergency_contact_name": f"{random.choice(['Nina', 'Carlos', 'Elena', 'Grace', 'Marcus', 'Linda', 'Victor', 'Julia'])} {random.choice(LAST_NAMES)}",
        "emergency_contact_relationship": random.choice(["Aunt", "Uncle", "Grandparent", "Family friend", "Neighbor"]),
        "emergency_contact_phone": make_phone(),
        "allergies": weighted_choice(ALLERGIES),
        "medical_conditions": weighted_choice(MEDICAL),
        "pediatrician_name": f"Dr. {random.choice(['Lee', 'Patel', 'Robinson', 'Chen', 'Goldstein', 'Morales'])}",
        "pediatrician_phone": make_phone(),
        "how_did_you_hear": weighted_choice(HOW_HEARD_OPTIONS),
        "notes": random.choice(NOTES),
        "photo_release": True,
        "enrollment_agreement": True,
    }
    return data, status, created_at


def set_submission_status(submission, status):
    if hasattr(submission, "status"):
        submission.status = status
        try:
            submission.save(update_fields=["status"])
            return
        except Exception:
            pass

    if isinstance(submission.data, dict):
        submission.data["status"] = status
        try:
            submission.save(update_fields=["data"])
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--replace", action="store_true", help="Delete existing submissions for this school first")
    args = parser.parse_args()

    try:
        school = School.objects.get(slug=SCHOOL_SLUG)
    except School.DoesNotExist:
        print(f"ERROR: school with slug '{SCHOOL_SLUG}' not found.", file=sys.stderr)
        return 1

    if args.replace:
        deleted, _ = Submission.objects.filter(school=school).delete()
        print(f"Deleted existing submissions: {deleted}")

    status_counts = Counter()
    program_counts = Counter()
    campus_counts = Counter()

    for idx in range(1, args.count + 1):
        data, status, created_at = build_submission(idx)
        submission = Submission.objects.create(
            school=school,
            form_key="default",
            data=data,
        )
        set_submission_status(submission, status)
        Submission.objects.filter(pk=submission.pk).update(created_at=created_at)

        status_counts[status] += 1
        program_counts[data["interested_in"]] += 1
        campus_counts[data["campus_preference"]] += 1

    print(f"Created {args.count} submissions for {school.display_name} ({school.slug})")
    print("Status distribution:", dict(status_counts))
    print("Program distribution:", dict(program_counts))
    print("Campus distribution:", dict(campus_counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())