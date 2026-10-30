from django import template

from scheduling.booking_policies import notice

register = template.Library()


@register.simple_tag
def customer_booking_copy(school):
    from scheduling.customer_experience import customer_copy
    return customer_copy(school)


@register.inclusion_tag("scheduling/booking_policies.html")
def booking_policy(school, kind):
    return {"policy_notice": notice(school, kind)}
