from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def material_amount(value):
    """Show measured stock without rounding or insignificant decimal zeroes."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""
    if not amount.is_finite():
        return ""
    return format(amount.normalize(), "f") if amount else "0"
