"""Prepare reviewed contract settings without launching, charging or replacing data."""
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from core.models import AdminAuditLog, School
from core.services.feature_flags import ALL_FLAGS, ALL_PLANS, PLAN_TRIAL


def load_customer_terms(slug):
    path = Path(settings.BASE_DIR) / "configs" / "customer_terms" / f"{slug}.json"
    terms = json.loads(path.read_text())
    if (not isinstance(terms, dict)
            or set(terms) != {"plan", "trial_duration_days", "feature_flags"}
            or terms["plan"] not in ALL_PLANS or terms["plan"] == PLAN_TRIAL
            or type(terms["trial_duration_days"]) is not int
            or not 1 <= terms["trial_duration_days"] <= 365
            or not isinstance(terms["feature_flags"], dict)
            or any(key not in ALL_FLAGS or type(value) is not bool
                   for key, value in terms["feature_flags"].items())):
        raise ValidationError("Invalid customer terms configuration.")
    return terms


def prepare_customer_terms(*, school_id, actor):
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied
    with transaction.atomic():
        school = School.objects.select_for_update().get(pk=school_id)
        terms = load_customer_terms(school.slug)
        if (not school.is_demo and not school.trial_pending_activation) or school.stripe_subscription_id:
            raise ValidationError("Only demo or unstarted customer-trial settings can be prepared.")
        flags = {**school.feature_flags, **terms["feature_flags"]}
        if (school.trial_requires_activation and school.trial_duration_days == terms["trial_duration_days"]
                and school.feature_flags == flags):
            return False
        before = {"trial_requires_activation": school.trial_requires_activation,
                  "trial_duration_days": school.trial_duration_days,
                  "feature_flags": school.feature_flags}
        school.trial_requires_activation = True
        school.trial_duration_days = terms["trial_duration_days"]
        school.feature_flags = flags
        school.save(update_fields=["trial_requires_activation", "trial_duration_days", "feature_flags"])
        AdminAuditLog.objects.create(
            actor=actor, action="action", model_label="core.school",
            object_id=str(school.pk), object_repr=school.slug,
            extra={"name": "customer_terms_prepared", "before": before, "terms": terms},
        )
    return True
