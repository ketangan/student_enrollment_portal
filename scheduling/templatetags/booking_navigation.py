from django import template
from scheduling.navigation import enrollment_url, school_entry

register = template.Library()


@register.filter
def booking_initials(name):
    return "".join(part[0] for part in str(name or "").split()[:2]).upper()


@register.simple_tag(takes_context=True)
def booking_school_entry(context, school):
    if "scheduling_entry" in context:
        return context["scheduling_entry"]
    return school_entry(context["request"].user, school)


@register.simple_tag(takes_context=True)
def booking_enrollment_url(context, profile):
    return enrollment_url(context["request"].user, profile)
