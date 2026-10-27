from django import template

from scheduling.booking_policies import notice

register = template.Library()


@register.inclusion_tag("scheduling/booking_policies.html")
def booking_policy(school, kind):
    return {"policy_notice": notice(school, kind)}
