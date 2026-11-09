from django import template

from scheduling.public_school_context import enrollment_navigation_context

register = template.Library()


@register.inclusion_tag("scheduling/school/public_navigation.html", takes_context=True)
def school_public_navigation(context, school):
    if context.get("embed_mode") or context.get("embed"):
        return {}
    return enrollment_navigation_context(school)
