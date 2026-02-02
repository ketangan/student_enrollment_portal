import logging
from django.core.management.base import BaseCommand, CommandError
from core.services.config_loader import load_school_config
from core.services.notifications import get_submission_email_config, send_submission_notification_email

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Send a test submission notification email"

    def add_arguments(self, parser):
        parser.add_argument("--school-slug", default="enrollment-request-demo")
        parser.add_argument("--student-name", required=True)
        parser.add_argument("--program", default="")
        parser.add_argument("--submission-id", default="999")

    def handle(self, *args, **opts):
        school_slug = opts["school_slug"]
        student_name = opts["student_name"]
        program = opts["program"]
        submission_id = opts["submission_id"]

        cfg_obj = load_school_config(school_slug)
        if not cfg_obj:
            raise CommandError(f"School config not found for slug={school_slug}")

        config_raw = getattr(cfg_obj, "raw", {}) or {}
        email_cfg = get_submission_email_config(config_raw)
        if not email_cfg:
            raise CommandError(
                f"No notifications.submission_email configured in YAML for slug={school_slug}"
            )

        self.stdout.write("=== Submission email config ===")
        self.stdout.write(f"from: {email_cfg.from_email}")
        self.stdout.write(f"to:   {email_cfg.to}")
        self.stdout.write(f"cc:   {email_cfg.cc}")
        self.stdout.write(f"bcc:  {email_cfg.bcc}")
        self.stdout.write("==============================")

        submission_data = {"program_interest": program} if program else {}

        try:
            ok = send_submission_notification_email(
                request=None,
                config_raw=config_raw,
                school_name=getattr(cfg_obj, "display_name", school_slug),
                submission_id=submission_id,
                student_name=student_name,
                submission_data=submission_data,
            )
        except Exception as e:
            logger.exception("Test email failed")
            raise CommandError(f"Test email failed: {e}")

        if not ok:
            raise CommandError("Email was skipped (missing config?)")

        self.stdout.write(self.style.SUCCESS("Test email sent (or handed to SMTP successfully)."))
        