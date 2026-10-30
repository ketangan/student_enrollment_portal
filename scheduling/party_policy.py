"""Operator-managed party intake policy; package prices remain database-owned."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from core.services.config_loader import load_school_config


def party_policy(school):
    config = load_school_config(school.slug)
    policy = (config.raw if config else {}).get("scheduling", {}).get("party_booking", {})
    if not isinstance(policy, dict) or type(policy.get("packages_only", False)) is not bool:
        raise ValidationError("Party booking policy needs a valid packages_only setting.")
    limit = policy.get("default_extra_participants", 0)
    maximum = policy.get("maximum_participants", 10000)
    if type(maximum) is not int or not 1 <= maximum <= 10000:
        raise ValidationError("Party booking policy needs a valid maximum participant count.")
    try:
        price = Decimal(str(policy.get("default_extra_price", "0")))
    except (InvalidOperation, ValueError):
        raise ValidationError("Party booking policy needs a valid extra participant price.")
    if (type(limit) is not int or not 0 <= limit <= 10000 or not price.is_finite()
            or not 0 <= price <= Decimal("999999.99") or price.as_tuple().exponent < -2):
        raise ValidationError("Party booking defaults need a valid participant limit and price.")
    return {"packages_only": policy.get("packages_only", False),
            "maximum_participants": maximum,
            "default_extra_participants": limit, "default_extra_price": price}
