"""Attribute background audit events to their business, not the payment mode."""
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError

from .portal_environment import current_environment


def infer_environment(log, registry=apps, using="default"):
    School = registry.get_model("core", "School")
    schools = School.objects.using(using)
    extra = log.extra if isinstance(log.extra, dict) else {}
    school = None
    try:
        if extra.get("school_id"):
            school = schools.filter(pk=extra["school_id"]).first()
        slug = extra.get("school_slug") or extra.get("school")
        if school is None and isinstance(slug, str):
            school = schools.filter(slug=slug).first()
        if school is None and extra.get("scheduling_profile_id"):
            Profile = registry.get_model("scheduling", "SchedulingProfile")
            school_id = Profile.objects.using(using).filter(pk=extra["scheduling_profile_id"]).values_list("school_id", flat=True).first()
            school = schools.filter(pk=school_id).first()
        if school is None and log.path:
            parts = log.path.strip("/").split("/")
            if len(parts) > 1 and parts[0] in {"schools", "bookings"}:
                school = schools.filter(slug=parts[1]).first()
        if school is None and log.model_label and log.object_id:
            model = registry.get_model(log.model_label.lower())
            if model == School:
                school = schools.filter(pk=log.object_id).first()
            else:
                # Only explicit tenant relationships; never infer ownership from the actor.
                for field_name, target, lookup in (("school", School, "school_id"),
                        ("profile", registry.get_model("scheduling", "SchedulingProfile"), "profile__school_id")):
                    try:
                        field = model._meta.get_field(field_name)
                    except FieldDoesNotExist:
                        continue
                    if field.is_relation and field.related_model == target:
                        school_id = model.objects.using(using).filter(pk=log.object_id).values_list(lookup, flat=True).first()
                        school = schools.filter(pk=school_id).first()
                        break
    except (LookupError, ValueError, TypeError, ValidationError):
        # Older events may refer to removed objects or retired model names.
        pass
    if school is not None:
        return "demo" if school.is_demo else "production"
    return current_environment.get()
