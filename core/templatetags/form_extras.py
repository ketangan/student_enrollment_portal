from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """
    Safely get dict-like items in Django templates.
    Returns "" if missing.
    """
    try:
        return mapping.get(key, "")
    except Exception:
        return ""
    