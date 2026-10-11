from pathlib import Path
import re

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
import yaml

from core.payments.models import PaymentAccount
from scheduling.capabilities import has_capability
from scheduling.models import SchedulingProfile

PREVIEW_DATABASE = "pontora_bookings_m1a_preview"


def local_database():
    database = settings.DATABASES["default"]
    return bool(settings.DEBUG and database["NAME"] == PREVIEW_DATABASE
                and database.get("HOST") in {"127.0.0.1", "localhost"})


def hosted_demo(slug):
    return bool(not settings.DEBUG
                and slug in getattr(settings, "BOOKING_HOSTED_DEMO_SLUGS", ())
                and getattr(settings, "BOOKING_HOSTED_DEMO_HOSTS", ()))


def allowed_request(request, slug):
    host = request.get_host().split(":")[0].lower()
    if local_database():
        return host in {"localhost", "127.0.0.1"}
    return bool(hosted_demo(slug) and request.is_secure()
                and host in getattr(settings, "BOOKING_HOSTED_DEMO_HOSTS", ()))


def available(profile, *, require_intake=False):
    return bool((local_database() or hosted_demo(profile.school.slug))
                and profile.enabled and profile.school.is_demo and profile.school.is_active
                and profile.school.feature_flags.get("booking_experience_demo") is True
                and (profile.native_reservations_enabled or not require_intake)
                and all(has_capability(profile, key) for key in ("workshops", "reservations")))


def configuration(slug):
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise Http404
    path = Path(settings.BASE_DIR) / "configs" / "booking_demos" / f"{slug}.yaml"
    if not path.is_file():
        raise Http404
    return yaml.safe_load(path.read_text())


def context(request, slug):
    if not allowed_request(request, slug):
        raise Http404
    profile = get_object_or_404(SchedulingProfile.objects.select_related("school"), school__slug=slug)
    if not available(profile):
        raise Http404
    config = configuration(slug)
    config["stripe_checkout"] = PaymentAccount.objects.filter(
        school=profile.school, environment="test", enabled=True,
    ).exists()
    config["design_previews"] = profile.school.feature_flags.get("booking_demo_inventory", {}).get("design_previews", {})
    bindings = profile.school.feature_flags.get("booking_demo_inventory", {}).get("groups", {})
    if bindings:
        from scheduling.models import ChoiceGroup
        groups = dict(ChoiceGroup.objects.filter(catalog__profile=profile, pk__in=bindings.values()).values_list("pk", "name"))
        config["original_groups"] = {key: config[key] for key in bindings}
        config["group_ids"] = bindings
        for key, pk in bindings.items():
            config[key] = groups.get(pk, config[key])
    return profile, config
