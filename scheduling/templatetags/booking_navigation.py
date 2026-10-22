from django import template
from scheduling.navigation import enrollment_url, school_entry

register = template.Library()


@register.simple_tag
def booking_design_guide(school):
    from django.urls import reverse
    from scheduling.models import DesignGuide
    if school and DesignGuide.objects.filter(profile__school=school, profile__enabled=True).exists():
        return reverse("scheduling:design_guide", kwargs={"school_slug": school.slug})
    return ""


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
