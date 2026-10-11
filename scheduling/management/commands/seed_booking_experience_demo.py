"""Opt-in fictional workshop data; never touches an existing customer's setup."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import School, SchoolAdminMembership
from scheduling.experience_demo.config import configuration
from scheduling.experience_demo import config as demo_config
from scheduling.models import Camp, ChoiceGroup, ChoiceOption, Location, Offering, Room, SchedulingMembership, SchedulingProfile, Session


def local_database():
    return demo_config.local_database()


def saved(model, defaults=None, **lookup):
    return model.objects.get_or_create(**lookup, defaults=defaults or {})[0]


class Command(BaseCommand):
    help = "Seed a fictional booking demo locally, or an explicitly allowlisted hosted demo."

    def add_arguments(self, parser):
        parser.add_argument("slug", choices=["plushumi-demo"])
        parser.add_argument("--password")
        parser.add_argument("--hosted", action="store_true", help="Allow additive setup for an explicitly enabled hosted demo. Does not enable password login.")
        parser.add_argument("--reset", action="store_true", help="Delete only the isolated local Plushumi rehearsal data before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        hosted = options.get("hosted", False)
        if hosted:
            if not demo_config.hosted_demo(options["slug"]) or options["reset"] or options["password"]:
                raise CommandError("Hosted setup requires explicit demo configuration; password assignment and reset are forbidden.")
        elif not local_database():
            raise CommandError("Only the isolated loopback preview database is allowed.")
        elif not options["password"]:
            raise CommandError("A local demo password is required.")
        slug, config = options["slug"], configuration(options["slug"])
        school = saved(School, slug=slug, defaults={"display_name": config["name"], "is_demo": True,
            "feature_flags": {"booking_experience_demo": True, "email_notifications_enabled": False, "ai_summary_enabled": False}})
        if (not school.is_demo or school.stripe_customer_id or school.stripe_subscription_id
                or school.smtp_host or school.app_fee_stripe_secret_key
                or school.feature_flags.get("booking_experience_demo") is not True):
            raise CommandError("Refusing a customer or externally connected school.")
        user, created = get_user_model().objects.get_or_create(username=config["owner"],
            defaults={"first_name": "Plushumi", "is_staff": not hosted})
        if user.is_superuser or SchoolAdminMembership.objects.filter(user=user).exists() or SchedulingMembership.objects.filter(user=user).exclude(profile__school=school).exists():
            raise CommandError("Refusing an operator, enrollment or shared account.")
        if hosted:
            if not created and not SchedulingMembership.objects.filter(user=user, profile__school=school).exists():
                raise CommandError("Refusing to adopt an existing account for a hosted demo.")
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
        else:
            user.is_active = user.is_staff = True
            user.set_password(options["password"])
            user.save(update_fields=["password", "is_active", "is_staff"])
        profile, profile_created = SchedulingProfile.objects.get_or_create(school=school, defaults={"enabled": True,
            "capabilities": ["workshops", "parties", "camps", "reservations", "integrations"], "native_reservations_enabled": True})
        saved(SchedulingMembership, profile=profile, user=user, defaults={"role": "owner"})
        if options["reset"]:
            from scheduling.experience_demo.reset import reset_demo
            reset_demo(profile)
        first = timezone.localdate() + timedelta(days=1)
        while first.weekday() != 5:
            first += timedelta(days=1)
        for position, place in enumerate(config["locations"]):
            location = saved(Location, profile=profile, name=place["name"], defaults={"timezone_name": place["zone"]})
            room = saved(Room, location=location, name="Workshop studio", defaults={"capacity": 12})
            offering = saved(Offering, location=location, name="Plushie-making workshop", defaults={"is_published": True,
                "accepts_requests": True, "accepts_party_requests": True, "description": "Choose your design and color, then create together."})
            groups = [(config["design_group"], config["designs"]), (config["color_group"], config["colors"]),
                (config["age_group"], [{"name": value} for value in ["Under 5", *map(str, range(5, 18)), "Adult", "Prefer not to say"]])]
            for order, (name, values) in enumerate(groups):
                group = saved(ChoiceGroup, offering=offering, name=name, defaults={"position": order,
                    "reference_image": config["design_image"] if order == 0 else ""})
                for index, choice in enumerate(values):
                    saved(ChoiceOption, group=group, label=choice["name"], defaults={"position": index, "swatch": choice.get("hex", "")})
            # Preserve dates and practice changes when the setup is run a second time.
            if not offering.sessions.exists():
                for days in (0, 7, 14):
                    for hour in (10, 13, 16):
                        start = datetime.combine(first+timedelta(days=days), time(hour), ZoneInfo(place["zone"]))
                        saved(Session, offering=offering, starts_at=start, defaults={"ends_at": start+timedelta(hours=2),
                            "room": room, "capacity": 12, "is_published": True})
            if position == 0:
                camp_offering = saved(Offering, location=location, name="Creative plushie camp", defaults={"is_published": True})
                camp, created = Camp.objects.get_or_create(offering=camp_offering, name="Three-day creative camp",
                    defaults={"price": "195.00", "deposit": "0.00", "online": False, "is_open": True})
                if created:
                    dates = []
                    for days in (2, 3, 4):
                        start = datetime.combine(first+timedelta(days=days), time(10), ZoneInfo(place["zone"]))
                        dates.append(saved(Session, offering=camp_offering, starts_at=start, defaults={"ends_at": start+timedelta(hours=2),
                            "room": room, "capacity": 12, "is_published": True}))
                    camp.sessions.set(dates)
        from scheduling.experience_demo.inventory_setup import seed_inventory
        seed_inventory(profile, config, user)
        if options["reset"] or (hosted and profile_created):
            from scheduling.experience_demo.material_samples import seed_reservations
            seed_reservations(profile, user, config)
        label = "Hosted" if hosted else "Local"
        self.stdout.write(self.style.SUCCESS(f"{label} booking demo ready: /bookings/{slug}/experience/ | {config['owner']}"))
