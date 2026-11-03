"""Install configured design photos without replacing school-managed uploads."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.http import Http404

from scheduling.experience_demo.config import configuration
from scheduling.models import ChoiceGroup, ChoiceOption, SchedulingProfile


class Command(BaseCommand):
    help = "Preview configured catalog photo updates for one school; use --apply to save."

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            profile = SchedulingProfile.objects.select_related("school").get(school__slug=options["school"])
            config = configuration(profile.school.slug)
        except (SchedulingProfile.DoesNotExist, Http404) as exc:
            raise CommandError("School or catalog photo configuration was not found.") from exc
        retired = config.get("retired_design_images", [])
        photos = {row["name"]: row for row in config.get("designs", []) if row.get("image_url")}
        coordinates = {(row.get("x"), row.get("y")): row for row in photos.values() if row.get("x")}
        flags = profile.school.feature_flags
        binding = flags.get("booking_demo_inventory", {})
        previews = dict(binding.get("design_previews", {}))
        groups = ChoiceGroup.objects.filter(Q(catalog__profile=profile) | Q(offering__location__profile=profile))
        groups = groups.filter(Q(name=config.get("design_group")) | Q(pk=binding.get("groups", {}).get("design_group")))
        changed = []
        for option in ChoiceOption.objects.filter(group__in=groups):
            old = previews.get(str(option.pk), {})
            photo = coordinates.get((old.get("x"), old.get("y"))) or photos.get(option.label)
            if not photo or option.image_url not in ["", *retired, photo["image_url"]]:
                continue
            if option.image_url != photo["image_url"]:
                option.image_url = photo["image_url"]
                changed.append(option)
            if str(option.pk) in previews:
                previews[str(option.pk)] = {**old, "image_url": photo["image_url"]}
        self.stdout.write(f"{len(changed)} design photos; {groups.filter(reference_image__in=retired).count()} retired group references.")
        if options["apply"]:
            ChoiceOption.objects.bulk_update(changed, ["image_url"])
            groups.filter(reference_image__in=retired).update(reference_image="")
            if previews != binding.get("design_previews", {}):
                profile.school.feature_flags = {**flags, "booking_demo_inventory": {**binding, "design_previews": previews}}
                profile.school.save(update_fields=["feature_flags"])
        else:
            self.stdout.write("Dry run; no data changed.")
