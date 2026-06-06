"""
Management command to seed SchoolProgram records from a school's YAML config.

Usage:
  python manage.py seed_school_programs_from_yaml --school <slug>
  python manage.py seed_school_programs_from_yaml --school <slug> --backfill-submissions

Idempotent — safe to re-run. Logs a summary.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import School, SchoolProgram, Submission
from core.services.config_loader import load_school_config


class Command(BaseCommand):
    help = "Seed SchoolProgram records from YAML and optionally backfill Submission.program FKs."

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True, help="School slug")
        parser.add_argument(
            "--field-key",
            default="",
            help="Override program_field_key (default: auto-detect from YAML or use existing)",
        )
        parser.add_argument(
            "--backfill-submissions",
            action="store_true",
            help="Also set Submission.program FK on existing rows",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without saving",
        )

    def handle(self, *args, **options):
        slug = options["school"]
        backfill = options["backfill_submissions"]
        dry_run = options["dry_run"]
        field_key_override = options["field_key"]

        try:
            school = School.objects.get(slug=slug)
        except School.DoesNotExist:
            raise CommandError(f"School '{slug}' not found.")

        try:
            config = load_school_config(slug)
        except Exception as exc:
            raise CommandError(f"Could not load YAML for '{slug}': {exc}")

        # Determine field key
        field_key = field_key_override or school.program_field_key
        if not field_key:
            # Try to auto-detect from YAML: find first select field with options
            form = getattr(config, "form", None) or {}
            for section in form.get("sections", []):
                for f in section.get("fields", []):
                    if f.get("type") == "select" and f.get("options"):
                        field_key = f["key"]
                        self.stdout.write(f"Auto-detected program field key: '{field_key}'")
                        break
                if field_key:
                    break

        if not field_key:
            raise CommandError("Could not determine program_field_key. Pass --field-key explicitly.")

        # Extract options from YAML
        form = getattr(config, "form", None) or {}
        yaml_options = []
        for section in form.get("sections", []):
            for f in section.get("fields", []):
                if f.get("key") == field_key and f.get("options"):
                    yaml_options = f["options"]
                    break

        if not yaml_options:
            raise CommandError(f"No options found for field '{field_key}' in YAML.")

        self.stdout.write(f"School: {school.display_name or slug}")
        self.stdout.write(f"Field key: {field_key}")
        self.stdout.write(f"YAML options ({len(yaml_options)}): {[o['value'] for o in yaml_options]}")
        self.stdout.write(f"Dry run: {dry_run}")
        self.stdout.write("")

        created = 0
        skipped = 0
        for opt in yaml_options:
            code = str(opt.get("value", "")).strip()
            label = str(opt.get("label", code)).strip()
            if not code:
                continue
            if SchoolProgram.objects.filter(school=school, code=code).exists():
                self.stdout.write(f"  SKIP (exists): {code} — {label}")
                skipped += 1
            else:
                if not dry_run:
                    SchoolProgram.objects.create(school=school, name=label, code=code)
                self.stdout.write(f"  CREATE: {code} — {label}")
                created += 1

        # Set program_field_key on school if not already set
        if school.program_field_key != field_key:
            if not dry_run:
                school.program_field_key = field_key
                school.save(update_fields=["program_field_key"])
            self.stdout.write(f"\nSet school.program_field_key = '{field_key}'")
        else:
            self.stdout.write(f"\nschool.program_field_key already = '{field_key}'")

        self.stdout.write(f"\nPrograms: {created} created, {skipped} skipped.")

        if not backfill:
            self.stdout.write("Pass --backfill-submissions to set Submission.program FKs on existing rows.")
            return

        # Backfill submissions
        self.stdout.write("\n--- Backfilling submissions ---")
        from collections import defaultdict
        programs = {p.code: p for p in SchoolProgram.objects.filter(school=school)}

        # Build normalized name → list of programs; only backfill when exactly one match.
        programs_by_name: dict[str, list] = defaultdict(list)
        for p in programs.values():
            programs_by_name[p.name.lower().strip()].append(p)

        submissions = Submission.objects.filter(school=school, program__isnull=True)
        matched = skipped_no_match = skipped_ambiguous = 0

        for sub in submissions:
            code_val = str((sub.data or {}).get(field_key, "")).strip()
            if not code_val:
                skipped_no_match += 1
                continue

            # Priority 1: exact code match
            prog = programs.get(code_val)

            # Priority 2: normalized name match (only when unambiguous)
            if prog is None:
                candidates = programs_by_name.get(code_val.lower(), [])
                if len(candidates) == 1:
                    prog = candidates[0]
                elif len(candidates) > 1:
                    self.stdout.write(
                        f"  AMBIGUOUS: submission #{sub.pk} value='{code_val}' "
                        f"matches {len(candidates)} programs — skipping"
                    )
                    skipped_ambiguous += 1
                    continue

            if prog is None:
                skipped_no_match += 1
            else:
                if not dry_run:
                    sub.program = prog
                    sub.save(update_fields=["program"])
                matched += 1

        self.stdout.write(
            f"Submissions backfilled: {matched} matched, "
            f"{skipped_no_match} no match, {skipped_ambiguous} ambiguous (skipped)."
        )
