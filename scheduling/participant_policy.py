"""Opt-in school age rules and bounded party estimates, shared by staff and customers."""
import re

from django import forms
from django.core.exceptions import ValidationError

from core.services.config_loader import load_school_config


def age_policy(school):
    config = load_school_config(school.slug)
    policy = (config.raw if config else {}).get("scheduling", {}).get("participant_age")
    if policy is None:
        return None
    if (not isinstance(policy, dict) or type(policy.get("minimum")) is not int
            or not 0 <= policy["minimum"] <= 130
            or not isinstance(policy.get("choice_group"), str) or not policy["choice_group"].strip()
            or not isinstance(policy.get("adult_label"), str) or not policy["adult_label"].strip()):
        raise ValidationError("Participant age policy needs a valid minimum, choice group and adult label.")
    return policy


def eligible_label(label, policy):
    return label == policy["adult_label"] or bool(re.fullmatch(r"[0-9]{1,3}", label)
        and policy["minimum"] <= int(label) <= 130)


class AgeRangeField(forms.CharField):
    def __init__(self, *args, minimum=None, **kwargs):
        self.minimum = minimum
        super().__init__(*args, **kwargs)

    def validate(self, value):
        super().validate(value)
        if not value or self.minimum is None:
            return
        if value.lower() in {"adult", "adults"}:
            return
        match = re.fullmatch(r"([0-9]{1,3})(?:\s*(?:-|\N{EN DASH}|to)\s*([0-9]{1,3}|adults?))?(?:\s+years?(?: old)?)?", value, re.IGNORECASE)
        if not match:
            raise ValidationError("Enter an age or age range, such as 7-9, or Adults.")
        lower = int(match[1])
        upper = 130 if (match[2] or "").lower() in {"adult", "adults"} else int(match[2] or match[1])
        if not self.minimum <= lower <= upper <= 130:
            raise ValidationError(f"Participants must be age {self.minimum} or older. Enter an increasing age range.")


def estimated_participants(value, *, maximum=10000):
    if not value:
        return
    match = re.fullmatch(r"([0-9]{1,5})(?:\s*(?:-|\N{EN DASH}|to)\s*([0-9]{1,5}))?(?:\s+guests?)?", value, re.IGNORECASE)
    if not match or not 1 <= int(match[1]) <= int(match[2] or match[1]) <= maximum:
        raise ValidationError(f"Enter a participant estimate such as 7-9 (1 to {maximum}).")


def estimate_upper_count(value, *, maximum=10000):
    estimated_participants(value, maximum=maximum)
    return int(re.findall(r"[0-9]+", value)[-1]) if value else None
